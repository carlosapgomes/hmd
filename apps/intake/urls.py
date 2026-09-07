"""URLs do intake do NIR (change intake-nir-upload).

Incluídas em ``config/urls.py`` sob o prefixo ``/intake/``. A raiz ``/intake/``
é a home de envio do relatório (slice 001); ``my_cases``/``case_detail``/
``serve_document`` (slice 004) ficam sob ``/intake/cases/`` — o identificador
de caso é sempre o UUID interno (nunca o path de storage). As ações de
revisão do gate (slice 005, D6) são POSTs sob o caso: ``gate/release/``
(liberar retido → ANONYMIZING com bypass) e ``gate/resubmit/`` (substituir
documentos e reprocessar), ambas escopadas ao criador.
"""

from django.urls import URLPattern, path

from . import views

app_name = "intake"

urlpatterns: list[URLPattern] = [
    path("", views.intake_home, name="home"),
    # Meus casos e detalhe do NIR (slice 004, D6/D9): acesso só do criador.
    path("cases/", views.my_cases, name="my_cases"),
    path("cases/<uuid:case_id>/", views.case_detail, name="case_detail"),
    path(
        "cases/<uuid:case_id>/documents/<int:document_id>/",
        views.serve_document,
        name="serve_document",
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
]
