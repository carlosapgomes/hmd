"""Asserção de renderização da thread multilinha (slice 002, R7).

O detalhe do NIR (``intake:case_detail``) renderiza ``communication.body``
com ``|linebreaksbr``: corpo multilinha (resposta final + linha de crédito)
separa por ``<br>`` no HTML, em vez de colar as linhas.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.communications import post_user_communication
from apps.cases.models import Case


@pytest.mark.django_db
def test_detail_renders_multiline_body_with_linebreaksbr(client: Client, nir_user: User) -> None:
    """R7: detalhe com corpo multilinha → ``<br`` no HTML da thread."""
    case = Case.objects.create(created_by=nir_user)
    body = (
        "Recusar o relatório — caso agendado na Unidade 2, que comunicará a Secretaria\n"
        "Médico responsável: Ana Souza — CRM 12345"
    )
    post_user_communication(case, user=nir_user, role="nir", body=body)

    client.force_login(nir_user)
    response = client.get(reverse("intake:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    html = response.content.decode()
    assert "Médico responsável: Ana Souza — CRM 12345" in html
    assert "<br" in html
