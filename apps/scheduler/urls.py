"""URLs do app scheduler (change scheduler-multi-unit, slice 003).

Incluídas em ``config/urls.py`` sob o prefixo ``/scheduler/``. A raiz é a fila
por estado com abas (aguardando/processados) e guard ``role_required``
(slice 003, R1/R2); o detalhe limitado (R3) fica sob ``/scheduler/case/<id>/``;
os PDFs do relatório original pós-decisão do próprio agendador (R7) por
position; os POSTs de ação confirmar/negar/desmarcar fecham as decisões do
estágio de agendamento (R4/R5) sobre os serviços dos slices 001/002.

case-work-lock, slice 004 (R1–R8, D4): ``scheduler:lock_renew`` e
``scheduler:lock_release`` (endpoints JSON do heartbeat e do release
best-effort do ``work_lock.js``) e ``scheduler:case_leave`` (POST server-side
do «Sair sem decidir» — libera a reserva e volta à fila).
"""

from django.urls import URLPattern, path

from . import views

app_name = "scheduler"

urlpatterns: list[URLPattern] = [
    path("", views.queue, name="queue"),
    path("partials/queue/", views.queue_partial, name="queue_partial"),
    # Histórico do agendador (slice 004, D6): busca por nº OU nome sobre o
    # universo com decisão de agendamento + detalhe em escopo histórico.
    path("historical/", views.historical_search, name="historical_search"),
    path(
        "historical/<uuid:case_id>/",
        views.historical_case_detail,
        name="historical_case_detail",
    ),
    path("case/<uuid:case_id>/", views.case_detail, name="case_detail"),
    path("case/<uuid:case_id>/pdf/<int:position>/", views.case_pdf, name="case_pdf"),
    # Viewer mobile do PDF original (slice PVP-004, R1): mesmo gate de
    # case_pdf; ?next= validado com resolve_safe_next_url.
    path(
        "case/<uuid:case_id>/pdf/<int:position>/viewer/",
        views.pdf_viewer,
        name="pdf_viewer",
    ),
    # Documentos e anexos recebidos via reapresentação scheduling_repeat
    # aplicada (slice 003, Unit 3C, D8 / R3.1–R3.4).
    path(
        "case/<uuid:case_id>/reappearances/<uuid:reappearance_id>/documents/<int:document_id>/",
        views.reappearance_document_serve,
        name="reappearance_document_serve",
    ),
    path(
        "case/<uuid:case_id>/reappearances/<uuid:reappearance_id>/documents/<int:document_id>/viewer/",
        views.reappearance_document_viewer,
        name="reappearance_document_viewer",
    ),
    path(
        "case/<uuid:case_id>/reappearances/<uuid:reappearance_id>/attachments/<int:attachment_id>/",
        views.reappearance_attachment_serve,
        name="reappearance_attachment_serve",
    ),
    path(
        "case/<uuid:case_id>/reappearances/<uuid:reappearance_id>/attachments/<int:attachment_id>/viewer/",
        views.reappearance_attachment_viewer,
        name="reappearance_attachment_viewer",
    ),
    path("case/<uuid:case_id>/confirm/", views.case_confirm, name="case_confirm"),
    path("case/<uuid:case_id>/deny/", views.case_deny, name="case_deny"),
    path("case/<uuid:case_id>/reopen/", views.case_reopen, name="case_reopen"),
    # case-work-lock (slice 004): reserva do caso na página de trabalho do
    # agendador — heartbeat, release best-effort e «Sair sem decidir».
    path("case/<uuid:case_id>/leave/", views.case_leave, name="case_leave"),
    path("case/<uuid:case_id>/lock/renew/", views.lock_renew, name="lock_renew"),
    path("case/<uuid:case_id>/lock/release/", views.lock_release, name="lock_release"),
]
