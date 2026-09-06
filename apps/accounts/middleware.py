"""Middlewares de conta e acesso do HMD (slices 005 e 006).

``ActiveRoleMiddleware`` (slice 005, R1/R6) garante que todo usuário
autenticado tenha um papel ativo único na sessão; ``IntranetGuardMiddleware``
(slice 006, R1–R6) bloqueia o papel ativo restrito (``nir``) fora da faixa de
intranet configurada.

Referência: ats-web ``apps/accounts/middleware.py`` (padrão clonado; D4/D5). O
papel ativo vive apenas na sessão — nunca persiste no banco.
"""

import ipaddress
import logging
from collections.abc import Callable

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.models import User

logger = logging.getLogger(__name__)

# Caminhos que não exigem papel ativo (D4/D5): autenticação e seleção de
# papel ficam fora do guard para não criar loop de redirect.
EXEMPT_PATHS = {"/login/", "/logout/", "/switch-role/"}
# Admin e estáticos seguem fluxo próprio (mesmo padrão do ats-web).
EXEMPT_PREFIXES = ("/admin/", "/static/")

NO_ROLES_MESSAGE = "Sua conta não possui papéis atribuídos. Contate o administrador."

# Mensagem clara de bloqueio do guard (R1) — sem stack trace nem detalhes
# internos de rede para o cliente.
INTRANET_BLOCKED_MESSAGE = "Acesso restrito à rede interna do hospital."


# ---------------------------------------------------------------------------
# Intranet guard (slice 006, R1–R6; ADR-0002)
# ---------------------------------------------------------------------------


def _get_client_ip(request: HttpRequest) -> str:
    """Extrai o IP real do cliente (R2).

    Usa o header confiável configurado (``settings.TRUSTED_PROXY_HEADER``,
    default ``HTTP_CF_CONNECTING_IP`` para o túnel Cloudflare) quando presente;
    caso contrário cai para ``REMOTE_ADDR``. Headers de proxy podem conter
    múltiplos IPs — considera-se o primeiro.
    """
    header = str(getattr(settings, "TRUSTED_PROXY_HEADER", ""))
    if header:
        value = request.META.get(header, "")
        if value:
            return str(value).split(",")[0].strip()
    return str(request.META.get("REMOTE_ADDR", ""))


def _is_intranet_ip(client_ip: str) -> bool:
    """True quando o IP pertence a alguma faixa de ``INTRANET_IP_RANGE``.

    ``INTRANET_IP_RANGE`` é uma lista de CIDRs separadas por vírgula (ex.:
    ``"127.0.0.0/8,192.168.15.0/24"``). Faixas inválidas ou vazias são
    ignoradas; IPs malformados não são considerados de intranet.
    """
    ip_range = getattr(settings, "INTRANET_IP_RANGE", "")
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for cidr in ip_range.split(","):
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


class IntranetGuardMiddleware:
    """Bloqueia papel ativo restrito fora da intranet (slice 006, R1).

    Regras em ordem:
    1. não autenticado → passa;
    2. papel ativo fora de ``INTRANET_RESTRICTED_ROLES`` → passa;
    3. path isento (login/logout/switch-role + static/media) → passa (R3);
    4. ``INTRANET_IP_RANGE`` vazia (default de dev) → passa (sem restrição);
    5. IP de origem dentro da intranet → passa;
    6. senão → HTTP 403 com mensagem clara (sem stack trace).

    Divergência deliberada vs ats-web (D5/ADR-0002, R4): a restrição segue o
    **papel ativo** da sessão, nunca o conjunto de papéis do usuário. A troca
    de papel via /switch-role/ (path isento) é a rota de fuga do usuário
    multi-role bloqueado com papel ativo restrito.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = request.user
        if not isinstance(user, User) or not user.is_authenticated:
            return self.get_response(request)

        active_role = request.session.get("active_role")
        if active_role not in getattr(settings, "INTRANET_RESTRICTED_ROLES", []):
            return self.get_response(request)

        if request.path in EXEMPT_PATHS or request.path.startswith(("/static/", "/media/")):
            return self.get_response(request)

        # Sem faixa configurada a restrição fica desligada: default seguro de
        # desenvolvimento (R6/critério de aceitação).
        if not getattr(settings, "INTRANET_IP_RANGE", ""):
            return self.get_response(request)

        client_ip = _get_client_ip(request)
        if _is_intranet_ip(client_ip):
            return self.get_response(request)

        logger.warning(
            "intranet_guard_blocked user=%s role=%s ip=%s path=%s",
            user.pk,
            active_role,
            client_ip,
            request.path,
        )
        return HttpResponseForbidden(INTRANET_BLOCKED_MESSAGE)


class ActiveRoleMiddleware:
    """Define o papel ativo único da sessão (R1)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = request.user
        if not isinstance(user, User):
            return self.get_response(request)
        if not user.is_authenticated:
            return self.get_response(request)
        if "active_role" in request.session:
            active_role = request.session["active_role"]
            if user.roles.filter(name=active_role).exists():
                return self.get_response(request)
            # Papel ativo revogado/removido do usuário: descarta da sessão e
            # segue a lógica normal (0 papéis → logout; N papéis → re-seleção;
            # 1 papel → auto-set) para não autorizar com papel antigo (R6).
            del request.session["active_role"]
        if request.path in EXEMPT_PATHS or request.path.startswith(EXEMPT_PREFIXES):
            return self.get_response(request)

        role_names = list(user.roles.order_by("name").values_list("name", flat=True))
        if len(role_names) == 1:
            # Um único papel: define automaticamente (R1).
            request.session["active_role"] = role_names[0]
        elif len(role_names) > 1:
            # Múltiplos papéis sem papel ativo: seleção obrigatória (R1).
            return redirect(reverse("switch_role"))
        elif len(role_names) == 0:
            # Sem papéis: encerra a sessão com mensagem explicativa (R1).
            logout(request)
            messages.error(request, NO_ROLES_MESSAGE)
            return redirect(reverse("login"))
        return self.get_response(request)
