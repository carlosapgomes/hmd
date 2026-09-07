"""Configuração de URLs raiz do HMD.

A home autenticada (``/``) e as páginas de conta (login/logout/perfil) vêm de
``apps.accounts.urls`` (slice 004); o admin usa o fluxo padrão do Django.
"""

from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path

urlpatterns: list[URLPattern | URLResolver] = [
    path("", include("apps.accounts.urls")),
    path("admin/", admin.site.urls),
    # Intake do NIR (change intake-nir-upload, slice 001): upload do relatório
    # sob /intake/; a raiz / continua home placeholder de apps.accounts.
    path("intake/", include("apps.intake.urls")),
]
