"""Anti-lockout local via cache do Django (change ad-kerberos, slice 004, D7).

Contadores de tentativas de login malsucedidas por CPF **normalizado**
(strip/lower — a mesma normalização do backend) e por par IP+CPF, no cache do
Django, com limiar/janela/duração configuráveis (``LOGIN_ATTEMPTS_LIMIT``,
``LOGIN_ATTEMPTS_WINDOW_SECONDS``, ``LOGIN_LOCKOUT_SECONDS``). O IP de origem
vem do mesmo helper confiável do guard de intranet (``_get_client_ip`` com
``TRUSTED_PROXY_HEADER``, reutilizado de ``apps/accounts/middleware.py``) —
nunca de header arbitrário (R1).

A checagem roda ANTES de qualquer backend/KDC: quem chama é a login view (R2).
O bloqueio é temporário (expira pelos TTLs do cache) e não altera
``account_status`` nem persiste em banco (R4).

Semântica por escopo (``cpf`` e ``ip+cpf``): um contador com TTL da janela
(``LOGIN_ATTEMPTS_WINDOW_SECONDS``) e, ao atingir o limiar, uma chave de
lockout com TTL da duração (``LOGIN_LOCKOUT_SECONDS``). A recusa vale enquanto
o contador estiver no limiar **ou** a chave de lockout existir — a duração
garante o mínimo de bloqueio mesmo se a janela expirar antes. Sucesso
(``clear_login_failures``) apaga contadores e lockouts do CPF.
"""

from django.conf import settings
from django.core.cache import cache as default_cache
from django.http import HttpRequest

from .middleware import _get_client_ip


def _normalize_cpf(username: str) -> str:
    """CPF de login normalizado: strip/lower (D3/D7).

    Mesma normalização do backend (``apps.accounts.backends``): variações de
    formatação não criam contadores diferentes.
    """
    return username.strip().lower()


def _cpf_keys(cpf: str) -> tuple[str, str]:
    """Chaves (contador, lockout) do escopo por CPF."""
    return f"login:failures:cpf:{cpf}", f"login:lockout:cpf:{cpf}"


def _ipcpf_keys(ip: str, cpf: str) -> tuple[str, str]:
    """Chaves (contador, lockout) do escopo por IP+CPF."""
    return f"login:failures:ip:{ip}:cpf:{cpf}", f"login:lockout:ip:{ip}:cpf:{cpf}"


def _scope_keys(cpf: str, ip: str) -> tuple[tuple[str, str], tuple[str, str]]:
    """Chaves dos dois escopos — CPF (vale entre IPs) e IP+CPF (por par)."""
    return _cpf_keys(cpf), _ipcpf_keys(ip, cpf)


def _limits() -> tuple[int, int, int]:
    """(limiar, janela, duração) lidos das settings por chamada (D7/R3).

    Lidos em tempo de chamada (não no import) para que os testes possam
    sobrescrever via ``override_settings``.
    """
    return (
        int(settings.LOGIN_ATTEMPTS_LIMIT),
        int(settings.LOGIN_ATTEMPTS_WINDOW_SECONDS),
        int(settings.LOGIN_LOCKOUT_SECONDS),
    )


def _increment(key: str, timeout: int) -> int:
    """Incrementa o contador ``key`` criando-o com ``timeout`` se não existir.

    ``cache.incr`` levanta ``ValueError`` quando a chave não existe — cria com
    ``add`` (TTL da janela) e re-tenta o incremento (fecha a corrida de duas
    criações simultâneas).
    """
    try:
        return int(default_cache.incr(key))
    except ValueError:
        if default_cache.add(key, 1, timeout):
            return 1
        return int(default_cache.incr(key))


def register_failed_login(request: HttpRequest, username: str) -> None:
    """Registra uma tentativa malsucedida nos contadores CPF e IP+CPF (R1/R2).

    Atingido o limiar em qualquer escopo, define a chave de lockout daquele
    escopo com TTL de ``LOGIN_LOCKOUT_SECONDS``.
    """
    cpf = _normalize_cpf(username)
    ip = _get_client_ip(request)
    limit, window, lockout = _limits()
    for counter_key, lockout_key in _scope_keys(cpf, ip):
        if _increment(counter_key, window) >= limit:
            default_cache.set(lockout_key, "1", lockout)


def is_login_locked(request: HttpRequest, username: str) -> bool:
    """True quando o CPF (ou o par IP+CPF) atingiu o limiar ou está em lockout.

    Chamada ANTES de consultar qualquer backend/KDC (R2). Não revela o motivo
    ao usuário — a view responde sempre com a mensagem genérica.
    """
    cpf = _normalize_cpf(username)
    ip = _get_client_ip(request)
    limit = int(settings.LOGIN_ATTEMPTS_LIMIT)
    for counter_key, lockout_key in _scope_keys(cpf, ip):
        count = default_cache.get(counter_key)
        if count is not None and int(count) >= limit:
            return True
        if default_cache.get(lockout_key) is not None:
            return True
    return False


def clear_login_failures(request: HttpRequest, username: str) -> None:
    """Zera contadores e lockouts do CPF (sucesso no login, R2/R4)."""
    cpf = _normalize_cpf(username)
    ip = _get_client_ip(request)
    keys: list[str] = []
    for counter_key, lockout_key in _scope_keys(cpf, ip):
        keys.extend((counter_key, lockout_key))
    default_cache.delete_many(keys)
