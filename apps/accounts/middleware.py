"""Middleware de papel ativo em sessão (slice 005, R1/R6).

``ActiveRoleMiddleware`` garante que todo usuário autenticado tenha um papel
ativo na sessão antes de acessar views protegidas:
- 1 papel → auto-set automático em ``session["active_role"]``;
- N papéis (N > 1) sem papel ativo → redirect para /switch-role/;
- 0 papéis → logout com mensagem explicativa;
- anônimo → passa direto (o fluxo de login cuida do resto).

Referência: ats-web ``apps/accounts/middleware.py`` (padrão clonado; D4). O
papel ativo vive apenas na sessão — nunca persiste no banco.
"""

from collections.abc import Callable

from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.models import User

# Caminhos que não exigem papel ativo (D4/D5): autenticação e seleção de
# papel ficam fora do guard para não criar loop de redirect.
EXEMPT_PATHS = {"/login/", "/logout/", "/switch-role/"}
# Admin e estáticos seguem fluxo próprio (mesmo padrão do ats-web).
EXEMPT_PREFIXES = ("/admin/", "/static/")

NO_ROLES_MESSAGE = "Sua conta não possui papéis atribuídos. Contate o administrador."


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
