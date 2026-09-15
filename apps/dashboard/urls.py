"""URLs do painel gerencial (change dashboard-notifications-pwa, slice 003).

Incluídas em ``config/urls.py`` sob o prefixo ``/dashboard/`` com namespace
``dashboard`` (apps NOVOS usam ``app_name``; só ``apps.accounts`` é global).

O encerramento administrativo (change painel-lista-encerramento, slice 003,
R2/D3) vive sob o caso, no padrão dos demais apps: ``case/<id>/close/`` é a
confirmação (GET com o catálogo de motivos) e ``case/<id>/close/submit/`` o POST
que delega ao serviço de fechamento.
"""

from django.urls import URLPattern, path

from . import views

app_name = "dashboard"

urlpatterns: list[URLPattern] = [
    path("", views.home, name="home"),
    path(
        "case/<uuid:case_id>/close/",
        views.admin_close_confirm,
        name="admin_close_confirm",
    ),
    path(
        "case/<uuid:case_id>/close/submit/",
        views.admin_close,
        name="admin_close",
    ),
]
