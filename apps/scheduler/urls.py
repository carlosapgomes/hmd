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
    path("case/<uuid:case_id>/", views.case_detail, name="case_detail"),
    path("case/<uuid:case_id>/pdf/<int:position>/", views.case_pdf, name="case_pdf"),
    path("case/<uuid:case_id>/confirm/", views.case_confirm, name="case_confirm"),
    path("case/<uuid:case_id>/deny/", views.case_deny, name="case_deny"),
    path("case/<uuid:case_id>/reopen/", views.case_reopen, name="case_reopen"),
    # case-work-lock (slice 004): reserva do caso na página de trabalho do
    # agendador — heartbeat, release best-effort e «Sair sem decidir».
    path("case/<uuid:case_id>/leave/", views.case_leave, name="case_leave"),
    path("case/<uuid:case_id>/lock/renew/", views.lock_renew, name="lock_renew"),
    path("case/<uuid:case_id>/lock/release/", views.lock_release, name="lock_release"),
]
