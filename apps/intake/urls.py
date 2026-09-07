"""URLs do intake do NIR (change intake-nir-upload).

Incluídas em ``config/urls.py`` sob o prefixo ``/intake/``. A raiz ``/intake/``
é a home de envio do relatório (slice 001); ``my_cases``/``case_detail``/
``serve_document`` (slice 004) ficam sob ``/intake/cases/`` — o identificador
de caso é sempre o UUID interno (nunca o path de storage).
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
]
