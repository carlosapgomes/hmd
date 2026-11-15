"""URLs do app doctor (change doctor-queue-decision, slices 002–004).

Incluídas em ``config/urls.py`` sob o prefixo ``/doctor/``. A raiz é a fila
por estado com filtro de subtipo e access control (slice 002); o detalhe do
caso re-identificado e o PDF original por position entram no slice 003; a
decisão por procedimento (GET form/POST submit atômico sobre o serviço do
change 03) fecha no slice 004.

case-work-lock, slice 003 (R1–R3, D3): ``doctor:lock_renew`` e
``doctor:lock_release`` (endpoints JSON do heartbeat e do release best-effort
do ``work_lock.js``) e ``doctor:case_leave`` (POST server-side do «Sair sem
decidir» — libera a reserva e volta à fila).
"""

from django.urls import URLPattern, path

from . import views

app_name = "doctor"

urlpatterns: list[URLPattern] = [
    path("", views.queue, name="queue"),
    path("partials/queue/", views.queue_partial, name="queue_partial"),
    path("case/<uuid:case_id>/", views.case_detail, name="case_detail"),
    path("case/<uuid:case_id>/pdf/<int:position>/", views.case_pdf, name="case_pdf"),
    # Viewer mobile do PDF original (slice PVP-003, R1): mesmo guard de
    # case_pdf; ?next= validado com resolve_safe_next_url.
    path(
        "case/<uuid:case_id>/pdf/<int:position>/viewer/",
        views.pdf_viewer,
        name="pdf_viewer",
    ),
    path("case/<uuid:case_id>/decide/", views.case_decide, name="case_decide"),
    path("case/<uuid:case_id>/leave/", views.case_leave, name="case_leave"),
    path("case/<uuid:case_id>/lock/renew/", views.lock_renew, name="lock_renew"),
    path("case/<uuid:case_id>/lock/release/", views.lock_release, name="lock_release"),
]
