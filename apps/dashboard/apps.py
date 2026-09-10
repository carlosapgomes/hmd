"""AppConfig de ``apps.dashboard`` (change dashboard-notifications-pwa, slice 003).

App do painel gerencial: serviços puros de métricas (``apps/dashboard/metrics.py``,
sem request) e a view ``dashboard:home`` (login-required, sem gate de papel —
painel transversal do D3). Sem models próprios neste slice (nenhuma migração).
"""

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dashboard"
    label = "dashboard"
    verbose_name = "Painel gerencial"
