"""URLs do app doctor (change doctor-queue-decision, slices 002–004).

Incluídas em ``config/urls.py`` sob o prefixo ``/doctor/``. A raiz é a fila
por estado com filtro de subtipo e access control (slice 002); o detalhe do
caso re-identificado e o PDF original por position entram no slice 003; a
decisão por procedimento (GET form/POST submit atômico sobre o serviço do
change 03) fecha no slice 004.
"""

from django.urls import URLPattern, path

from . import views

app_name = "doctor"

urlpatterns: list[URLPattern] = [
    path("", views.queue, name="queue"),
    path("case/<uuid:case_id>/", views.case_detail, name="case_detail"),
    path("case/<uuid:case_id>/pdf/<int:position>/", views.case_pdf, name="case_pdf"),
    path("case/<uuid:case_id>/decide/", views.case_decide, name="case_decide"),
]
