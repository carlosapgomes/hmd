"""Cliente Kerberos AD com failover restrito (change ad-kerberos, slice 002).

Taxonomia ``KerberosAuthResult`` (R2), wrapper real do minikerberos 0.4.9
``validate_password`` (R3) e ``validate_with_failover`` (R4) que percorre
``AD_DCS`` avançando **somente** em erro de transporte (D5): códigos KDC de
autenticação são definitivos (repetir a senha em outro DC pode contar dobrado
para lockout do AD) e ``52`` (resposta grande p/ UDP) é reprocessado via TCP
no mesmo DC antes de qualquer classificação. Resposta do KDC que o minikerberos
não diagnostica (sentinela ``ERR_NOT_FOUND``/``0xFFFFFF``, código inextrável) é
classe transporte — avança ao próximo DC (P1).

O wrapper é injetável via ``settings.KERBEROS_CLIENT_FACTORY`` (D6): o default
aponta esta função real; a suíte injeta fakes e **nunca** toca a rede. A
validação contra os DCs reais é o comando manual ``ad_check`` (slice 003/D9).
"""

import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from asysocks.unicomm.common.target import UniProto
from django.conf import settings
from django.utils.module_loading import import_string
from minikerberos.client import KerbrosClient
from minikerberos.common.creds import KerberosCredential
from minikerberos.common.target import KerberosTarget
from minikerberos.protocol.errors import KerberosError

# Códigos KRB-ERROR (RFC 4120). ``25`` (PREAUTH_REQUIRED) é passo interno do
# ``get_TGT`` do minikerberos — nunca resultado final. ``52``
# (RESPONSE_TOO_BIG) é erro de resposta grande para UDP: o wrapper reprocessa
# via TCP no mesmo DC antes de classificar (D5).
DEFINITIVE_AUTH_CODES: frozenset[int] = frozenset({6, 18, 23, 24, 37})
KRB_ERR_RESPONSE_TOO_BIG = 52
# Sentinela do minikerberos 0.4.9: quando o código bruto do KRB-ERROR não
# converte ao enum ``KerberosErrorCode``, ``errorcode`` vira ``ERR_NOT_FOUND``
# (= 0xFFFFFF). Para o wrapper, código igual ao sentinela/inextrável significa
# "KDC respondeu algo não diagnosticado" → classe transporte (failover) — ver
# ``_classify_kerberos_error``.
MINIKERBEROS_SENTINEL_CODE = 0xFFFFFF
# reasons que autorizam o failover a avançar para o próximo DC.
TRANSPORT_REASONS: frozenset[str] = frozenset({"kdc_unreachable", "kdc_timeout"})

# Factory injetável (D6): recebe (upn, senha, dc, timeout) e devolve o
# resultado de UMA tentativa contra UM DC.
ClientFactory = Callable[[str, str, str, int], "KerberosAuthResult"]


@dataclass(frozen=True)
class KerberosAuthResult:
    """Resultado imutável de uma validação Kerberos (R2/D6).

    ``ok`` sucesso; ``code`` código KRB-ERROR extraído por atributo de
    protocolo (nunca texto); ``reason`` classificação de falha
    (``kdc_error``/``kdc_timeout``/``kdc_unreachable``/``all_kdcs_unreachable``).
    """

    ok: bool
    code: int | None = None
    reason: str | None = None


def _split_upn(upn: str) -> tuple[str, str]:
    """Divide o UPN em ``(principal, realm)``.

    O realm é o sufixo do UPN em maiúsculas (D3) — floresta multi-domínio,
    nunca um realm fixo.
    """
    principal, separator, suffix = upn.rpartition("@")
    if not separator or not principal or not suffix:
        raise ValueError(f"UPN inválido (esperado usuario@dominio): {upn!r}")
    return principal, suffix.upper()


def _extract_minikerberos_code(exc: BaseException) -> int | None:
    """Extrai o código KRB-ERROR de atributos do objeto de exceção (D6/P1).

    Em 0.4.9 o ``errorcode`` do ``KerberosError`` é um enum que colapsa para o
    sentinela ``ERR_NOT_FOUND`` quando o código bruto não converte — o código
    bruto real vive na estrutura nativa (``krb_err_msg``/``native``). Por isso
    a ordem é: (1) código bruto do dict nativo (chave ``error-code``); (2)
    queda para atributo int ou enum (``.value``). Resultado igual ao sentinela
    (``0xFFFFFF``) = código inexistente → ``None`` (alimenta a classificação
    de transporte do ``validate_password``). Nunca se lê texto da mensagem.
    """
    for carrier_attr in ("krb_err_msg", "krb_error", "error", "err"):
        carrier = getattr(exc, carrier_attr, None)
        if carrier is None:
            continue
        native = getattr(carrier, "native", None)
        if native is None and isinstance(carrier, dict):
            native = carrier
        if isinstance(native, dict):
            raw = native.get("error-code")
            if isinstance(raw, int):
                if raw == MINIKERBEROS_SENTINEL_CODE:
                    return None
                return raw

    for attr in ("errorcode", "error_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            if value == MINIKERBEROS_SENTINEL_CODE:
                return None
            return value
        if value is not None:
            enum_value = getattr(value, "value", None)
            if isinstance(enum_value, int):
                if enum_value == MINIKERBEROS_SENTINEL_CODE:
                    return None
                return enum_value

    return None


def _classify_kerberos_error(exc: KerberosError) -> KerberosAuthResult:
    """Classifica um ``KerberosError`` em duas camadas (P1).

    Código de protocolo REAL extraído → ``reason="kdc_error"`` com o ``code``:
    política atual mantida — os definitivos {6, 18, 23, 24, 37} não sofrem
    failover no nível acima (anti-lockout) e ``52`` é reprocessado via TCP
    pelo chamador. Código inextrável ou igual ao sentinela do minikerberos
    (resposta do KDC não diagnosticada, ex.: serviço indisponível) →
    ``reason="kdc_unreachable"`` sem ``code`` — classe transporte, elegível ao
    failover.
    """
    code = _extract_minikerberos_code(exc)
    if code is None:
        return KerberosAuthResult(False, reason="kdc_unreachable")
    return KerberosAuthResult(False, code=code, reason="kdc_error")


# Decisão (revisão P1): ``socket.setdefaulttimeout`` é global ao PROCESSO
# (não por thread). Sem proteção, o ``finally`` de uma tentativa concorrente
# (ex.: runserver dev em threads/gthread) pode remover o timeout ainda em uso
# por outra tentativa. A janela completa — leitura do default anterior,
# ``setdefaulttimeout``, ``get_TGT`` e restauração — é serializada por um lock
# de módulo. O HMD tem volume baixo de logins: serializar é aceitável.
_ATTEMPT_TIMEOUT_LOCK = threading.Lock()


def _authenticate_once(
    principal: str,
    realm: str,
    password: str,
    dc: str,
    timeout: int,
    protocol: UniProto,
) -> None:
    """Executa ``get_TGT()`` com cliente novo contra um DC (D2).

    Uma tentativa = um cliente; o TGT obtido serve apenas de prova da senha e
    é descartado junto com o cliente (nada é retido, sem ccache).

    Timeout: o socket síncrono do minikerberos 0.4.9 (``KerberosClientSocket``)
    não expõe parâmetro de timeout — os sockets são criados bloqueantes sem
    ``settimeout``. O timeout por tentativa é então aplicado via
    ``socket.setdefaulttimeout`` (default de socket global do processo, lido
    pelos sockets criados dentro de ``get_TGT``) e restaurado em ``finally``.
    A janela inteira é serializada pelo lock de módulo (decisão acima): o
    default global nunca pertence a duas tentativas ao mesmo tempo.
    """
    cred = KerberosCredential()
    cred.username = principal
    cred.domain = realm
    cred.password = password

    target = KerberosTarget(dc, protocol=protocol, timeout=timeout)
    target.domain = realm

    client = KerbrosClient(cred, target)

    with _ATTEMPT_TIMEOUT_LOCK:
        previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            client.get_TGT()
        finally:
            socket.setdefaulttimeout(previous_timeout)


def validate_password(upn: str, password: str, dc: str, timeout: int) -> KerberosAuthResult:
    """Valida a senha AD contra um DC (R3).

    Tentativa inicial via UDP; ``KRB_ERR_RESPONSE_TOO_BIG`` (52) — resposta
    grande demais para UDP — é reprocessada via TCP no **mesmo DC** antes de
    qualquer classificação (D5). ``KerberosError`` é classificado em duas
    camadas (P1): código real extraído vira ``code`` de protocolo com
    ``reason="kdc_error"``; código inextrável/sentinela vira
    ``reason="kdc_unreachable"`` (classe transporte, sem ``code``).
    ``OSError``/``TimeoutError`` viram ``reason`` de transporte sem código.
    """
    principal, realm = _split_upn(upn)

    try:
        _authenticate_once(principal, realm, password, dc, timeout, UniProto.CLIENT_UDP)
    except KerberosError as exc:
        first = _classify_kerberos_error(exc)
        if first.code != KRB_ERR_RESPONSE_TOO_BIG:
            return first
        try:
            _authenticate_once(principal, realm, password, dc, timeout, UniProto.CLIENT_TCP)
        except KerberosError as exc:
            return _classify_kerberos_error(exc)
        except TimeoutError:
            return KerberosAuthResult(False, reason="kdc_timeout")
        except OSError:
            return KerberosAuthResult(False, reason="kdc_unreachable")
        return KerberosAuthResult(ok=True)
    except TimeoutError:
        return KerberosAuthResult(False, reason="kdc_timeout")
    except OSError:
        return KerberosAuthResult(False, reason="kdc_unreachable")

    return KerberosAuthResult(ok=True)


def _resolve_client_factory() -> ClientFactory:
    """Resolve a factory configurada em ``settings.KERBEROS_CLIENT_FACTORY``.

    Default é o dotted-path do wrapper real; testes injetam fakes diretamente
    (callable) via ``override_settings`` — zero rede na suíte.
    """
    configured: Any = settings.KERBEROS_CLIENT_FACTORY
    if isinstance(configured, str):
        return cast(ClientFactory, import_string(configured))
    return cast(ClientFactory, configured)


def validate_with_failover(upn: str, password: str) -> KerberosAuthResult:
    """Valida a senha percorrendo ``AD_DCS`` na ordem (R4/D5).

    Avança ao próximo DC apenas quando a tentativa anterior foi de transporte
    (``kdc_unreachable``/``kdc_timeout``); retorna imediatamente em sucesso ou
    código definitivo ({6, 18, 23, 24, 37}). Esgotados os DCs →
    ``all_kdcs_unreachable``.
    """
    factory = _resolve_client_factory()
    dcs: list[str] = list(settings.AD_DCS)
    timeout: int = settings.AD_KDC_TIMEOUT

    for dc in dcs:
        result = factory(upn, password, dc, timeout)
        if result.ok:
            return result
        if result.code in DEFINITIVE_AUTH_CODES:
            # Definitivo: repetir a senha em outro DC pode contar dobrado para
            # o lockout do AD — sem failover.
            return result
        if result.reason in TRANSPORT_REASONS:
            continue
        # Nem sucesso, nem código definitivo, nem transporte (ex.: código
        # Kerberos inesperado ou resultado desconhecido): failover é restrito
        # a transporte.
        return result

    return KerberosAuthResult(False, reason="all_kdcs_unreachable")
