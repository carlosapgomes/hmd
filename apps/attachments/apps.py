"""AppConfig de ``apps.attachments`` (change attachment-processing-ocr).

App dono dos anexos clínicos: model ``CaseAttachment`` + validação própria
(slice 001); extração híbrida/worker/vision/signals nos slices 002/003. O
slice 002 registra o trigger de processamento no ``ready()`` (padrão
``apps.pipeline``): ``CaseEvent.post_save`` de ``CASE_ANONYMIZATION_COMPLETED``
→ enqueue do worker via ``transaction.on_commit``.
"""

from django.apps import AppConfig


class AttachmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.attachments"
    label = "attachments"
    verbose_name = "Anexos"

    def ready(self) -> None:
        """Registra os signals do app (enqueue do worker de anexos, slice 002)."""
        from apps.attachments import signals  # noqa: F401
