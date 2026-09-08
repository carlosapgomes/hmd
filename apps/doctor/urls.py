"""URLs do app doctor (change doctor-queue-decision, slice 002).

Incluídas em ``config/urls.py`` sob o prefixo ``/doctor/``. A raiz é a fila
por estado com filtro de subtipo e access control (este slice); o detalhe do
caso e a decisão por procedimento chegam nos slices 003/004.
"""

from django.urls import URLPattern, path

from . import views

app_name = "doctor"

urlpatterns: list[URLPattern] = [
    path("", views.queue, name="queue"),
]
