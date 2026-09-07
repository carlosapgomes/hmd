"""AppConfig de ``apps.anonymization`` (change presidio-anonymization).

App de barreira de privacidade: slices 001–003 entregaram a pré-extração
determinística pura (``deterministic.py``), recognizers BR + engine singleton
(``recognizers.py``/``engine.py``) e o serviço com artefatos no ``Case``
(``services.py``). O slice 004 registra o signal de enqueue da task de
anonimização na entrada de ``ANONYMIZING`` (``ready()`` → ``signals.py``) e a
task do worker (``tasks.py``).
"""

from django.apps import AppConfig


class AnonymizationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.anonymization"
    label = "anonymization"
    verbose_name = "Anonimização"

    def ready(self) -> None:
        """Registra os signals do app (enqueue na entrada de ANONYMIZING, slice 004)."""
        from apps.anonymization import signals  # noqa: F401
