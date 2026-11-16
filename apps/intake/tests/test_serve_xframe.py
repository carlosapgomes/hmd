"""Header X-Frame-Options: SAMEORIGIN no serve de documento (slice PVP-001, R4).

O ``XFrameOptionsMiddleware`` nega framing por padrão (DENY); o decorator
``@xframe_options_sameorigin`` na view ``intake:serve_document`` libera o
framing same-origin para o ``<embed>`` dos cards. Guards, corpo e
``Cache-Control`` existentes seguem inalterados (cobertos pelos testes de
``test_my_cases.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.intake.services import create_case_with_documents

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.cases.models import Case

NIR_ROLE = "nir"


def _create_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> Case:
    """Cria um caso NEW com 1 PDF (estado controlado pelo teste)."""
    return create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type="art_perif",
    )


@pytest.mark.django_db
def test_serve_document_sameorigin(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: serve_document responde X-Frame-Options: SAMEORIGIN (embed liberado)."""
    case = _create_case(nir_user, pdf_factory)
    document = list(case.documents.all())[0]

    client.force_login(nir_user)
    response = client.get(reverse("intake:serve_document", args=[case.case_id, document.pk]))

    assert response.status_code == 200
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
