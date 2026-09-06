"""Context processors globais de ``apps.accounts`` (slice 004, R3).

Expoem o nome de exibição do app (``APP_DISPLAY_NAME``) a todos os templates
via navbar/títulos. O contexto de papel ativo da sessão chega no slice 005
(middleware/switch-role).
"""

from django.conf import settings
from django.http import HttpRequest


def app_display_name(request: HttpRequest) -> dict[str, str]:
    """Adiciona ``app_display_name`` ao contexto de todos os templates."""
    return {"app_display_name": settings.APP_DISPLAY_NAME}
