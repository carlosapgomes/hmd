"""AppConfig de ``apps.doctor`` (change doctor-queue-decision, slice 002).

App da fila médica: fila por estado com filtro de subtipo sob access control
fechado (matriz D2, design D5). Os slices 003/004 adicionam o detalhe
re-identificado + PDF e a decisão por procedimento. Sem ``ready()`` neste
change — nenhum signal registrado.
"""

from django.apps import AppConfig


class DoctorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.doctor"
    label = "doctor"
    verbose_name = "Fila médica"
