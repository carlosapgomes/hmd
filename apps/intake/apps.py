"""AppConfig de ``apps.intake`` (change intake-nir-upload, slice 001).

App de porta de entrada do NIR: envio do relatório multi-PDF com declaração
de tipos (este slice) e, nos slices seguintes, processamento assíncrono e a
tela de meus casos. Sem ``ready()`` neste change — nenhum signal registrado.
"""

from django.apps import AppConfig


class IntakeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.intake"
    label = "intake"
    verbose_name = "Intake (NIR)"
