"""Backend sintético EXCLUSIVO da verificação (ui-parity-foundation D7b).

Autentica os usuários de papel criados pelo ``seed_verify`` pelo form real
de login — sem Kerberos/AD (fora de escopo nesta fase) e sem cookie injetado.
O ``LocalAccountBackend`` só aceita superusuário sem ``ad_upn``; logo, o seed
sozinho não autenticaria os usuários de papel.

Guard fail-closed no import: este módulo recusa carregar sob qualquer settings
que não seja positivamente ``config.settings.verify`` (``SETTINGS_MODULE``
ativo — settings programáticas ou módulo vazio recusam mesmo com banco
``hmd_verify_*``; o nome do banco segue conferido como defesa em
profundidade). Dev/prod/test nunca importam este backend (não consta em
``AUTHENTICATION_BACKENDS`` deles) e, se importarem, o import levanta
``ImproperlyConfigured``.
"""

import os
import re
from typing import Any

from django.core.exceptions import ImproperlyConfigured

_VERIFY_DB_NAME_RE = re.compile(r"hmd_verify_[a-z0-9_]+")


def _assert_verify_settings() -> None:
    """Recusa carregar fora de ``config.settings.verify`` (D7b).

    Identidade positiva e fail-closed: com settings configuradas, o módulo
    ativo (``settings.SETTINGS_MODULE``) precisa ser exatamente
    ``config.settings.verify`` — settings programáticas (``configure()``),
    módulo vazio ou qualquer outro módulo recusam, mesmo com banco
    ``hmd_verify_*``. Sem settings configuradas, só o ambiente pode
    atestar, e apenas com o valor exato.
    """
    from django.conf import settings

    if not settings.configured:
        module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
        if module != "config.settings.verify":
            raise ImproperlyConfigured(
                "backends_verify só carrega sob config.settings.verify "
                f"(DJANGO_SETTINGS_MODULE={module!r}, settings não configuradas)."
            )
        return
    settings_module: str | None = settings.SETTINGS_MODULE
    if settings_module != "config.settings.verify":
        raise ImproperlyConfigured(
            "backends_verify só carrega sob config.settings.verify "
            f"(settings ativas={settings_module!r})."
        )
    name = str(settings.DATABASES.get("default", {}).get("NAME", ""))
    if not _VERIFY_DB_NAME_RE.fullmatch(name) or "prod" in name:
        raise ImproperlyConfigured(
            f"backends_verify só carrega com banco hmd_verify_* (banco configurado={name!r})."
        )


# Guard ANTES dos imports de auth/models: fora de verify, o módulo falha
# sem carregar nenhum model do app.
_assert_verify_settings()

from django.contrib.auth.backends import BaseBackend  # noqa: E402
from django.http import HttpRequest  # noqa: E402

from apps.accounts.models import User  # noqa: E402


def _normalize_username(username: str) -> str:
    """Mesma normalização dos demais backends: strip/lower."""
    return username.strip().lower()


class VerifySyntheticBackend(BaseBackend):
    """Autentica usuários sintéticos do seed pelo form real (verificação só).

    Regras: usuário existente, ``is_active``, ``account_status == "active"``
    e senha conferida pelo hash local. Sem ``ad_upn``, sem superusuário como
    requisito — é a identidade sintética do harness, nunca uma credencial
    hospitalar.
    """

    def authenticate(
        self,
        request: HttpRequest | None = None,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> User | None:
        if username is None or password is None:
            return None
        try:
            user = User.objects.get(username=_normalize_username(username))
        except User.DoesNotExist:
            User().set_password(password)  # mesmo custo p/ inexistente
            return None
        if not user.is_active or user.account_status != "active":
            return None
        return user if user.check_password(password) else None

    def get_user(self, user_id: Any) -> User | None:
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
        return user if user.is_active and user.account_status == "active" else None
