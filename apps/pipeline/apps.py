"""AppConfig de ``apps.pipeline`` (change llm-pipeline-per-type, slice 001)."""

from django.apps import AppConfig


class PipelineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.pipeline"
    label = "pipeline"
    verbose_name = "Pipeline LLM"
