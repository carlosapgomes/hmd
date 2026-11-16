"""URLs do painel gerencial (change dashboard-notifications-pwa, slice 003).

Incluídas em ``config/urls.py`` sob o prefixo ``/dashboard/`` com namespace
``dashboard`` (apps NOVOS usam ``app_name``; só ``apps.accounts`` é global).

O detalhe do caso (change painel-ats-parity, slice 002, R2/D2) é o destino do
botão [Detalhes] da lista: ``case/<id>/`` mostra a identificação completa e
hospeda a ação de encerramento. O encerramento administrativo (change
painel-lista-encerramento, slice 003, R2/D3) vive sob o caso, no padrão dos
demais apps: ``case/<id>/close/`` é a confirmação (GET com o catálogo de
motivos) e ``case/<id>/close/submit/`` o POST que delega ao serviço de
fechamento.
"""

from django.urls import URLPattern, path

from . import views

app_name = "dashboard"

urlpatterns: list[URLPattern] = [
    path("", views.home, name="home"),
    path(
        "case/<uuid:case_id>/",
        views.case_detail,
        name="case_detail",
    ),
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
    path("case/<uuid:case_id>/pdf/<int:position>/", views.case_pdf, name="case_pdf"),
    # Viewer mobile do PDF original (slice PVP-005, R1): mesmo guard do
    # detalhe; ?next= validado com resolve_safe_next_url.
    path(
        "case/<uuid:case_id>/pdf/<int:position>/viewer/",
        views.pdf_viewer,
        name="pdf_viewer",
    ),
    path(
        "case/<uuid:case_id>/attachments/<int:attachment_id>/",
        views.serve_attachment,
        name="serve_attachment",
    ),
    # Viewer do anexo PDF (slice PVP-007, R1): SÓ application/pdf (imagens
    # usam link direto + <img loading=lazy>); mesmo guard de serve_attachment.
    path(
        "case/<uuid:case_id>/attachments/<int:attachment_id>/viewer/",
        views.attachment_pdf_viewer,
        name="attachment_pdf_viewer",
    ),
]
