"""Backends de autenticação do HMD (change ad-kerberos-authentication, slice 003).

``AUTHENTICATION_BACKENDS`` lista os dois backends em ordem fixa (design D4):
Kerberos primeiro, local apenas para break-glass.

- ``KerberosBackend``: usuários provisionados com ``ad_upn`` (UPN completo
  ``cpf@dominio``) autenticam exclusivamente via Active Directory (AS-REQ).
  Usuário inexistente ou **sem** ``ad_upn`` → ``None`` sem consultar o KDC;
  ``account_status`` é checado ANTES do AS-REQ (conta bloqueada não gera
  tráfego Kerberos). Quando a validação falha por indisponibilidade
  (``all_kdcs_unreachable``/``kdc_timeout``), marca
  ``request.kerberos_unavailable`` (contrato request-scoped, D4) — a login
  view exibe "serviço indisponível", distinto de credenciais inválidas.
- ``LocalAccountBackend`` (modificado): **apenas superusuários sem ``ad_upn``**
  autenticam localmente (ADR-0003: break-glass é do superusuário) e somente
  com ``AD_ALLOW_LOCAL_AUTH=True`` (default ``False`` em base; dev/test setam
  ``True``). Usuários com ``ad_upn`` são recusados sempre; usuários comuns sem
  ``ad_upn`` também — a credencial local transitória deixou de existir.
"""

import logging
from typing import Any

from django.conf import settings
from django.contrib.auth.backends import BaseBackend, ModelBackend
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest

from apps.accounts.models import User

from .kerberos import validate_with_failover

logger = logging.getLogger(__name__)

# Resultados que significam "o serviço AD está fora" — nunca "credenciais
# inválidas" (D4/D5). A login view usa a marcação para escolher a mensagem.
UNAVAILABLE_REASONS = frozenset({"all_kdcs_unreachable", "kdc_timeout"})


def _normalize_username(username: str) -> str:
    """CPF de login normalizado: strip/lower (D3).

    A mesma normalização usada pelo anti-lockout (slice 004): variações de
    formatação não criam identidades nem contadores diferentes.
    """
    return username.strip().lower()


def _load_user(username: str) -> User | None:
    """Carrega o usuário pelo CPF normalizado; inexistente → ``None``."""
    try:
        return User.objects.get(username=username)
    except User.DoesNotExist:
        return None


def _is_active_account(user: User) -> bool:
    """Conta autenticável: ``is_active`` e ``account_status == "active"``."""
    return bool(user.is_active) and user.account_status == "active"


class KerberosBackend(BaseBackend):
    """Autentica usuários com ``ad_upn`` via Kerberos AS-REQ (R1).

    O login digitado é o CPF (``sAMAccountName``); o UPN completo — com o
    realm derivado do sufixo — vem do ``ad_upn`` cadastrado (D3). O wrapper
    constrói um cliente novo por tentativa e descarta o TGT (D2): nenhuma
    senha/ccache é retida.
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
        user = _load_user(_normalize_username(username))
        # Blank-aware (P1): ``CharField(null=True, blank=True)`` pode persistir
        # string vazia (``""``) via ORM sem ``full_clean`` — vazio = sem UPN.
        if user is None or not user.ad_upn:
            # Não é um usuário AD: outro backend decide (sem tráfego Kerberos).
            return None
        if not _is_active_account(user):
            # Status antes do AS-REQ (D4): conta bloqueada não gera tráfego
            # Kerberos nem conta para lockout do AD.
            return None

        result = validate_with_failover(user.ad_upn, password)
        if result.ok:
            return user

        # Log interno com código/razão de protocolo — sem username/CPF (CPF é
        # PII e o usuário externo nunca recebe esses detalhes, D5).
        logger.warning(
            "kerberos_login_failed code=%s reason=%s",
            result.code,
            result.reason,
        )
        if result.reason in UNAVAILABLE_REASONS:
            self._mark_kerberos_unavailable(request)
        return None

    @staticmethod
    def _mark_kerberos_unavailable(request: HttpRequest | None) -> None:
        """Contrato request-scoped (D4): a login view lê o atributo."""
        if request is not None:
            setattr(request, "kerberos_unavailable", True)

    def get_user(self, user_id: Any) -> User | None:
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
        return user if _is_active_account(user) else None


class LocalAccountBackend(ModelBackend):
    """Break-glass local (ADR-0003/D4): superusuário sem ``ad_upn``.

    Herda a checagem de senha do ``ModelBackend`` e adiciona as regras
    herméticas: ``AD_ALLOW_LOCAL_AUTH`` ligada (default ``False``), usuário
    sem ``ad_upn`` e superusuário — qualquer outro perfil é recusado.
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
        if not getattr(settings, "AD_ALLOW_LOCAL_AUTH", False):
            return None
        user = _load_user(_normalize_username(username))
        if user is None:
            # Mesmo custo do ModelBackend para usuário inexistente (mitiga
            # enumeração por tempo de resposta).
            User().set_password(password)
            return None
        if user.ad_upn or not user.is_superuser:
            # Regra hermética (R2, blank-aware P1): usuário AD nunca autentica
            # local (``ad_upn`` vazio = sem UPN); usuário comum sem ad_upn
            # também não — restou o break-glass superuser.
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def user_can_authenticate(self, user: User | AnonymousUser | None) -> bool:
        """Usuário autentica somente se ativo e com ``account_status`` ativo."""
        if user is None or isinstance(user, AnonymousUser):
            return False
        if not user.is_active:
            return False
        if not super().user_can_authenticate(user):
            return False
        return user.account_status == "active"
