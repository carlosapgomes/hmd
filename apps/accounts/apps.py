"""AppConfig de ``apps.accounts`` (slice 003).

O ``ready()`` registra os signals do app (change dashboard-notifications-pwa,
slice 001): ``CaseEvent.post_save`` de evento-marco → notificações in-app
(padrão ``apps.attachments``).
"""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Contas e Acesso"

    def ready(self) -> None:
        """Registra os signals do app (notificações por marcos, slice 001)."""
        from apps.accounts import signals  # noqa: F401
