"""URLs do intake do NIR (change intake-nir-upload).

Incluídas em ``config/urls.py`` sob o prefixo ``/intake/``. A raiz ``/intake/``
é a home de envio do relatório (slice 001); ``my_cases``/``case_detail``/
``serve_document`` (slice 004) ficam sob ``/intake/cases/`` — o identificador
de caso é sempre o UUID interno (nunca o path de storage). As ações de
revisão do gate (slice 005, D6) são POSTs sob o caso: ``gate/release/``
(liberar retido → ANONYMIZING com bypass) e ``gate/resubmit/`` (substituir
documentos e reprocessar), ambas escopadas ao criador. O ack do fechamento
(slice 003 do nir-result-closure, R2) é POST ``cases/<id>/ack/`` escopado ao
criador apenas no estado ``FINAL_REPLY_POSTED``.
"""

from django.urls import URLPattern, path

from . import views

app_name = "intake"

urlpatterns: list[URLPattern] = [
    path("", views.intake_home, name="home"),
    # Meus casos e detalhe do NIR (slice 004, D6/D9): acesso só do criador.
    path("cases/", views.my_cases, name="my_cases"),
    # Fragmento de "meus casos" para o auto-refresh htmx de 20 s
    # (ui-parity-queues slice 003, R3/D1): mesmo assembly da página cheia.
    path("cases/partials/list/", views.my_cases_partial, name="my_cases_partial"),
    path("cases/<uuid:case_id>/", views.case_detail, name="case_detail"),
    path(
        "cases/<uuid:case_id>/documents/<int:document_id>/",
        views.serve_document,
        name="serve_document",
    ),
    # Viewer mobile do PDF original (slice PVP-002, R1): mesmo guard de
    # criador do serve_document; ?next= validado com resolve_safe_next_url.
    path(
        "cases/<uuid:case_id>/documents/<int:document_id>/viewer/",
        views.pdf_viewer,
        name="pdf_viewer",
    ),
    path(
        "cases/<uuid:case_id>/attachments/<int:attachment_id>/",
        views.serve_attachment,
        name="serve_attachment",
    ),
    # Viewer do anexo PDF (slice PVP-006, R2): SÓ application/pdf (imagens
    # usam link direto + <img loading=lazy>); mesmo guard de criador do serve.
    path(
        "cases/<uuid:case_id>/attachments/<int:attachment_id>/viewer/",
        views.attachment_pdf_viewer,
        name="attachment_pdf_viewer",
    ),
    # Confirmação explícita do procedimento (confirm-nir-procedure-review,
    # slice 002, D2): POST do próprio criador sobre caso marcado retido por
    # divergência — resolve pela confirmação com reprocessamento real.
    path(
        "cases/<uuid:case_id>/procedure-review/confirm/",
        views.procedure_review_confirm,
        name="procedure_review_confirm",
    ),
    # Conferência NIR de reapresentação (reapresentacao-casos-filas, slice 005,
    # D2): POST em escopo de equipe para responder same_request / independent.
    path(
        "cases/<uuid:case_id>/reappearance/respond/",
        views.reappearance_respond,
        name="reappearance_respond",
    ),
    # Ações do gate NIR (slice 005, D6/D7): apenas caso retido do próprio criador.
    path(
        "cases/<uuid:case_id>/gate/release/",
        views.gate_release,
        name="gate_release",
    ),
    path(
        "cases/<uuid:case_id>/gate/resubmit/",
        views.gate_resubmit,
        name="gate_resubmit",
    ),
    # Confirmação de recebimento da resposta final (nir-result-closure, 003):
    # POST escopado ao criador no estado FINAL_REPLY_POSTED → CLEANED.
    path(
        "cases/<uuid:case_id>/ack/",
        views.case_ack,
        name="case_ack",
    ),
    # Reenvio corrigido de caso encerrado (nir-result-closure, 004, D4): GET
    # form / POST serviço, escopado ao criador no estado CLEANED → NOVO caso.
    path(
        "cases/<uuid:case_id>/resubmit/",
        views.case_resubmit,
        name="case_resubmit",
    ),
    # Busca NIR de encerrados + detalhe histórico (paridade-ats, slice 003,
    # D6): universo histórico de qualquer criador (rota própria com guarda de
    # escopo — os guards criador das rotas de «meus casos» ficam intactos).
    # O detalhe histórico responde GET (read-only) e POST (abertura da
    # intercorrência em caso elegível).
    path("closed-cases/", views.closed_cases_search, name="closed_cases_search"),
    path(
        "closed-cases/<uuid:case_id>/",
        views.closed_case_detail,
        name="closed_case_detail",
    ),
]
