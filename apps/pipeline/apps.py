"""AppConfig de ``apps.pipeline`` (change llm-pipeline-per-type, slice 001).

O slice 006 registra os signals de enqueue do pipeline (R5): entrada real em
``LLM_EXTRACTING`` (source ANONYMIZING) e retomada pós-bypass
(``LLM_SUMMARIZING`` com source ``LLM_EXTRACTING`` E ator user) — ambos via
``transaction.on_commit``.
"""

from django.apps import AppConfig


class PipelineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.pipeline"
    label = "pipeline"
    verbose_name = "Pipeline LLM"

    def ready(self) -> None:
        """Registra os signals do app (enqueue do pipeline LLM, slice 006)."""
        from apps.pipeline import signals  # noqa: F401
