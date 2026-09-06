"""Configuração de URLs raiz do HMD.

Home estática mínima (slice 001) como health check; a home real por papel e as
páginas de conta chegam nos slices 003–004.
"""

from django.contrib import admin
from django.urls import URLPattern, URLResolver, path
from django.views.generic import TemplateView

urlpatterns: list[URLPattern | URLResolver] = [
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
    path("admin/", admin.site.urls),
]
