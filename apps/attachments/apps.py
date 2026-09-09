"""AppConfig de ``apps.attachments`` (change attachment-processing-ocr, slice 001).

App dono dos anexos clínicos (model ``CaseAttachment`` + validação própria em
``apps/attachments/services.py`` neste slice; worker/extração/verificação nos
slices 002/003). Sem ``ready()`` neste change — nenhum signal registrado.
"""

from django.apps import AppConfig


class AttachmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.attachments"
    label = "attachments"
    verbose_name = "Anexos"
