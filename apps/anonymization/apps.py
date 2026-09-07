"""AppConfig de ``apps.anonymization`` (change presidio-anonymization, slice 001).

App de barreira de privacidade: este slice entrega apenas a pré-extração
determinística pura (``deterministic.py``) — sem models, sem recognizers
(slice 002) e sem serviços/campos de ``Case`` (slice 003). Sem ``ready()``
neste change — nenhum signal registrado.
"""

from django.apps import AppConfig


class AnonymizationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.anonymization"
    label = "anonymization"
    verbose_name = "Anonimização"
