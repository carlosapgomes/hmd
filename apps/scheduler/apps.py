"""AppConfig de ``apps.scheduler`` (change scheduler-multi-unit, slice 001).

App do agendador: os serviços transacionais de confirmar/negar chegam neste
slice (D2 — dono dos serviços de agendamento); a fila/UI do papel scheduler
chega no slice 003. Sem models neste change e sem ``ready()`` — nenhum signal
registrado.
"""

from django.apps import AppConfig


class SchedulerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.scheduler"
    label = "scheduler"
    verbose_name = "Agendamento"
