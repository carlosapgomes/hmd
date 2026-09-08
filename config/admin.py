"""Django admin customizado do HMD (change admin-local-identity, slice 001).

O login do Django admin usa a view própria do Django — sem hook de view. A
solução (design D3) é a subclass ``HmdAdminSite``: o ``login()`` envolve o
fluxo padrão com (1) pre-check do anti-lockout local (bloqueado recusa cedo,
sem tentativa de autenticação) e (2) pós-processo que conta APENAS falhas
determinísticas de credencial do perfil administrativo local (superusuário
sem ``ad_upn``) e zera os contadores no sucesso.

O wiring usa ``HmdAdminConfig(AdminConfig)`` com ``default_site``: o
``admin.site`` global (lazy do Django) passa a resolver para ``HmdAdminSite``
e os ``admin.site.register`` existentes continuam válidos sem mudança (R4).

Nota de importação: este módulo é importado durante a população do app
registry (módulo do app config do admin, antes dos models) — imports de
models/domain são LAZY (dentro dos métodos), mesmo padrão dos inner-imports
de ``django/contrib/admin/sites.py``.
"""

from typing import Any, cast

from django.contrib import admin
from django.contrib.admin.apps import AdminConfig
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache


def _normalize_username(username: str) -> str:
    """Username normalizado: strip/lower (regra do backend e do anti-lockout).

    Mesma normalização usada por ``apps.accounts.backends`` e pelo
    anti-lockout local — variações de formatação não desviam dos contadores
    nem da resolução do perfil (D3/R3b).
    """
    return username.strip().lower()


def _is_local_admin_profile(username: str) -> bool:
    """True quando o username (normalizado) é o perfil administrativo local.

    Perfil local por design (ADR-0009): superusuário **sem** ``ad_upn``. Só
    falhas deste perfil contam no /admin — falhas de outros perfis (com
    ``ad_upn``, assistenciais) não (D3/R3d).
    """
    from apps.accounts.models import User

    try:
        user = User.objects.get(username=_normalize_username(username))
    except User.DoesNotExist:
        return False
    return user.is_superuser and not user.ad_upn


class HmdAdminSite(admin.AdminSite):
    """Admin site com o login coberto pelo anti-lockout local (D3)."""

    @method_decorator(never_cache)
    @login_not_required
    def login(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        """Login do admin: pre-check de lockout e contagem só do perfil local.

        Envolve o fluxo padrão do ``AdminSite.login``:

        1. POST com username bloqueado (``is_login_locked``) → recusa cedo:
           200 com o form re-renderizado e a mensagem genérica, SEM chamar
           ``super().login`` (nenhum backend/KDC é consultado) e sem nova
           contagem (R3a);
        2. POST do fluxo padrão que falha (200) e cujo username é o perfil
           administrativo local com senha não-vazia → ``register_failed_login``
           (falha determinística de credencial local — R3b); POST com sucesso
           (redirect) → ``clear_login_failures`` (R3c). Outros perfis (com
           ``ad_upn``) não contam nesta rota (R3d).
        """
        from apps.accounts.ratelimit import (
            clear_login_failures,
            is_login_locked,
            register_failed_login,
        )

        if request.method == "POST":
            username = request.POST.get("username", "")
            if is_login_locked(request, username):
                return self._locked_login_response(request, extra_context)

        response = super().login(request, extra_context=extra_context)

        if request.method == "POST":
            username = request.POST.get("username", "")
            password = request.POST.get("password", "")
            if response.status_code == 302:
                # Sucesso no /admin: zera os contadores do CPF (R3c).
                clear_login_failures(request, username)
            elif password and _is_local_admin_profile(username):
                # Falha determinística do perfil local no /admin (R3b). POST
                # sem senha é form inválido, não falha de credencial — não
                # conta. Perfis AD (falha por Kerberos/indisponibilidade)
                # também não contam nesta rota (R3d).
                register_failed_login(request, username)
        return response

    def _locked_login_response(
        self, request: HttpRequest, extra_context: dict[str, Any] | None
    ) -> HttpResponse:
        """Re-renderiza o login do admin como falha genérica, sem autenticar.

        Reproduz o fluxo padrão do ``AdminSite.login`` com um form que falha
        deterministicamente em ``clean()``: a recusa do anti-lockout nunca
        chega a ``authenticate`` e a mensagem exibida é exatamente a genérica
        de credenciais inválidas do admin — indistinguível de uma senha errada
        (não revela o bloqueio, R3a).
        """
        from django.contrib.admin.forms import AdminAuthenticationForm
        from django.contrib.auth.views import LoginView
        from django.core.exceptions import ValidationError
        from django.urls import reverse

        class LockedLoginForm(AdminAuthenticationForm):
            def clean(self) -> dict[str, Any]:
                # Falha determinística antes de qualquer autenticação: mesma
                # mensagem genérica do fluxo padrão, sem ``super().clean()``
                # (que consultaria os backends) e sem revelar o bloqueio.
                raise ValidationError(
                    self.error_messages["invalid_login"],
                    code="invalid_login",
                    params={"username": self.username_field.verbose_name},
                )

        context = {
            **self.each_context(request),
            "title": _("Log in"),
            "subtitle": None,
            "app_path": request.get_full_path(),
            "username": request.POST.get("username", ""),
        }
        if REDIRECT_FIELD_NAME not in request.GET and REDIRECT_FIELD_NAME not in request.POST:
            context[REDIRECT_FIELD_NAME] = reverse("admin:index", current_app=self.name)
        context.update(extra_context or {})

        defaults = {
            "extra_context": context,
            "authentication_form": LockedLoginForm,
            "template_name": self.login_template or "admin/login.html",
        }
        request.current_app = self.name
        return cast(HttpResponse, LoginView.as_view(**defaults)(request))


class HmdAdminConfig(AdminConfig):
    """App config do admin com o site custom (R4).

    ``default_site`` faz o ``admin.site`` global (lazy do Django) resolver
    para ``HmdAdminSite``; ``name`` herdado mantém o app como
    ``django.contrib.admin`` — autodiscover e ``admin.site.register``
    existentes continuam válidos sem mudança.
    """

    default_site = "config.admin.HmdAdminSite"
