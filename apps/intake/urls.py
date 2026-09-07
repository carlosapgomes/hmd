"""URLs do intake do NIR (change intake-nir-upload, slice 001).

Incluídas em ``config/urls.py`` sob o prefixo ``/intake/``. A raiz ``/``
continua sendo a home placeholder de ``apps.accounts``; as telas de meus
casos/detalhe chegam no slice 004.
"""

from django.urls import URLPattern, path

from . import views

app_name = "intake"

urlpatterns: list[URLPattern] = [
    path("", views.intake_home, name="home"),
]
