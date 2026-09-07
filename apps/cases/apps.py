"""AppConfig de ``apps.cases`` (change 03, slice 001)."""

from django.apps import AppConfig


class CasesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cases"
    label = "cases"
    verbose_name = "Casos"
