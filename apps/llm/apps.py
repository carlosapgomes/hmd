"""AppConfig de ``apps.llm`` (change llm-pipeline-per-type, slice 003)."""

from django.apps import AppConfig


class LlmConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.llm"
    label = "llm"
    verbose_name = "Prompts LLM"
