"""URLs do painel gerencial (change dashboard-notifications-pwa, slice 003).

Incluídas em ``config/urls.py`` sob o prefixo ``/dashboard/`` com namespace
``dashboard`` (apps NOVOS usam ``app_name``; só ``apps.accounts`` é global).
"""

from django.urls import URLPattern, path

from . import views

app_name = "dashboard"

urlpatterns: list[URLPattern] = [
    path("", views.home, name="home"),
]
