"""Context processors globais de ``apps.accounts``.

Expoem a todos os templates: o nome de exibição do app (``APP_DISPLAY_NAME``),
o contexto de papel ativo da sessão (slice 005, R3) — papel ativo, lista de
papéis do usuário e flag ``has_multiple_roles`` — e a contagem de notificações
não lidas (slice 002 do change 11, R2).
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest

from apps.accounts.models import User
from apps.accounts.notifications import get_unread_notification_count


def app_display_name(request: HttpRequest) -> dict[str, str]:
    """Adiciona ``app_display_name`` ao contexto de todos os templates."""
    return {"app_display_name": settings.APP_DISPLAY_NAME}


def role_context(request: HttpRequest) -> dict[str, object]:
    """Expõe papel ativo, papéis do usuário e flag multi-role (R3).

    Usuário anônimo recebe valores vazios; o contexto nunca persiste nada no
    banco — apenas lê ``session["active_role"]`` e os papéis do usuário.
    """
    user = request.user
    if not isinstance(user, User) or not user.is_authenticated:
        return {"active_role": "", "user_roles": [], "has_multiple_roles": False}

    user_roles = list(user.roles.order_by("name").values_list("name", flat=True))
    return {
        "active_role": request.session.get("active_role", ""),
        "user_roles": user_roles,
        "has_multiple_roles": len(user_roles) > 1,
    }


def notification_unread_count(request: HttpRequest) -> dict[str, int]:
    """Expõe a contagem de notificações não lidas (badge global, D2).

    Usuário anônimo recebe 0 (nada de consulta sem destinatário); o valor é a
    fonte única consumida pelo sino de ``templates/base.html`` em toda página
    autenticada.
    """
    user = request.user
    if not isinstance(user, User) or not user.is_authenticated:
        return {"notification_unread_count": 0}
    return {"notification_unread_count": get_unread_notification_count(user)}
