"""Backend de autenticação local transitória (slice 004, R1).

Estágio 1 do ADR-0003: o backend delega a verificação de senha ao
``ModelBackend`` padrão (senha hasheada no banco) e adiciona a checagem de
``account_status`` — somente contas ``active`` autenticam. Contas
``blocked``/``removed`` são recusadas com a mesma mensagem genérica de
credenciais inválidas (a UI nunca revela o motivo interno). O change
``ad-kerberos-authentication`` substitui este mecanismo.
"""

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import AnonymousUser

from apps.accounts.models import User


class LocalAccountBackend(ModelBackend):
    """``ModelBackend`` + recusa de contas ``blocked``/``removed``.

    A checagem de senha continua sendo a do ``ModelBackend``
    (``check_password``); aqui adicionamos apenas a exigência de
    ``account_status == "active"`` sobre o usuário autenticável.
    """

    def user_can_authenticate(self, user: User | AnonymousUser | None) -> bool:
        """Usuário autentica somente se ativo e com ``account_status`` ativo."""
        if user is None or isinstance(user, AnonymousUser):
            return False
        if not user.is_active:
            return False
        if not super().user_can_authenticate(user):
            return False
        return user.account_status == "active"
