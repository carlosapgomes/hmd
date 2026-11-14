"""Paridade visual do detalhe/decisão médico (ui-parity-intake-detail, slice 003).

R1/R3: o detalhe read-only e a página de trabalho/decisão usam o layout
ats-web (``doctor-decision-layout`` + cards ``doctor-report-card`` com
blocos ``report-block``/``doctor-report-grid`` e cartão de formulário
``doctor-decision-form-card``), com o MESMO conteúdo e a MESMA ordem
clínica (contrato de ``doctor-detail-context``). Escopo estreito: só
marcadores/conteúdo — nenhum comportamento é exercido aqui.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.attachments.models import AttachmentStatus, CaseAttachment, ExtractionMethod, PatientMatch
from apps.cases.models import Case, CaseProcedure, CaseStatus

DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"

# Ordem clínica contratada (doctor-detail-context) — pinada aqui como
# guarda visual: o restyle não pode reordenar os cards.
_CLINICAL_ORDER = (
    "Procedimentos do caso",
    "Sumário clínico",
    "Estrutura extraída",
    "Prior-case",
    "Documentos",
    "Requisitos gerais acionáveis",
    "Alertas consultivos",
)

# Ordem COMPLETA com os cards condicionais (P2-3): «Anexos» (só com anexo
# no caso) e «Recomendação agregada do pipeline» (só com sugestão agregada
# do LLM2) entram entre «Documentos»→«Requisitos» e «Requisitos»→«Alertas»
# respectivamente, conforme _case_cards.html e _WORK_PAGE_CARD_ORDER.
_FULL_CLINICAL_ORDER = (
    "Procedimentos do caso",
    "Sumário clínico",
    "Estrutura extraída",
    "Prior-case",
    "Documentos",
    "Anexos",
    "Requisitos gerais acionáveis",
    "Recomendação agregada do pipeline",
    "Alertas consultivos",
)


def _advance_to_awaiting_doctor(case: Case) -> None:
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _make_awaiting_case(created_by: User, procedure_types: Sequence[str]) -> Case:
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(case=case, procedure_type=procedure_type, declared_by_nir=True)
    _advance_to_awaiting_doctor(case)
    Case.objects.filter(pk=case.pk).update(
        patient_name="Paciente Visual Medico",
        agency_record_number="33345",
        patient_age=84,
        patient_gender="F",
        patient_race="Parda",
    )
    case.summary_text = "Resumo visual: claudicação intermitente."
    case.save(update_fields=["summary_text"])
    case.refresh_from_db()
    return case


def _make_populated_case(created_by: User) -> Case:
    """Caso em AWAITING_DOCTOR com os dois cards condicionais renderizaveis.

    Espelha os padroes aprovados de ``test_decision._make_work_page_case``:
    anexo ``PROCESSED``/``LOCAL_PDF`` para «Anexos» e
    ``suggested_action['aggregate']`` para «Recomendacao agregada».
    Nenhum fluxo de backend/seed novo — so factories/ORM ja aprovados.
    """
    case = _make_awaiting_case(created_by, (ANGIO_TYPE,))
    CaseAttachment.objects.create(
        case=case,
        content_type="application/pdf",
        original_filename="laudo-anexo.pdf",
        size_bytes=1024,
        uploaded_by=created_by,
        status=AttachmentStatus.PROCESSED,
        extraction_method=ExtractionMethod.LOCAL_PDF,
        patient_match=PatientMatch.MATCH,
        verification_summary="Laudo anexado ao caso.",
    )
    case.suggested_action = {
        "aggregate": {
            "suggestion": "recusar",
            "motivos": ["plaquetas abaixo do minimo da secao S1"],
        },
    }
    case.save(update_fields=["suggested_action"])
    case.refresh_from_db()
    return case


def _login(client: Client, user: User, role: str) -> None:
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _card_title_index(body: str, title: str) -> int:
    marker = f'<h2 class="h5 mb-0">{title}</h2>'
    index = body.find(marker)
    assert index != -1, f"card «{title}» ausente no detalhe"
    return index


@pytest.mark.django_db
def test_doctor_detail_uses_ats_web_report_layout(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: detalhe read-only no layout ats-web, conteúdo idêntico por papel."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-visual-detalhe", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    # Marcadores do layout ats-web (novos nesta slice).
    assert "doctor-decision-layout" in body
    assert "doctor-report-card" in body
    assert "doctor-report-grid" in body
    assert "report-block" in body
    assert "report-block-body" in body
    # Conteúdo idêntico ao layout antigo.
    assert "Paciente Visual Medico" in body
    assert "33345" in body
    assert "84 a" in body
    assert "Resumo visual: claudicação intermitente." in body
    assert "Arteriografia periférica" in body
    # Ordem clínica preservada (contrato doctor-detail-context).
    indices = [_card_title_index(body, title) for title in _CLINICAL_ORDER]
    assert indices == sorted(indices)


@pytest.mark.django_db
def test_doctor_detail_orders_conditional_cards_when_populated(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """P2-3: cards condicionais na posição contratada quando o caso os tem.

    Com anexo processado e sugestão agregada no caso, «Anexos» e
    «Recomendação agregada do pipeline» devem aparecer nas posições exatas
    da ordem completa — nao apenas os cards sempre-presentes.
    """
    case = _make_populated_case(nir_user)
    doctor = user_factory("medico-visual-condicional", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Anexos" in body
    assert "Recomendação agregada do pipeline" in body
    indices = [_card_title_index(body, title) for title in _FULL_CLINICAL_ORDER]
    assert indices == sorted(indices)


@pytest.mark.django_db
def test_doctor_decide_uses_report_layout_and_form_card(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R1: página de trabalho/decisão no layout ats-web, formulário intacto."""
    case = _make_awaiting_case(nir_user, (ANGIO_TYPE,))
    doctor = user_factory("medico-visual-decide", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_decide", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "doctor-decision-layout" in body
    assert "doctor-report-card" in body
    assert "doctor-report-grid" in body
    assert "report-block-body" in body
    assert "doctor-decision-form-card" in body
    # Formulário/reserva intactos (só classes/estrutura mudam).
    assert 'id="doctor-decision-form"' in body
    assert 'name="lock_token"' in body
    assert "Decisão médica por procedimento" in body
    # Leitura completa antes do formulário (ordem única).
    indices = [_card_title_index(body, title) for title in _CLINICAL_ORDER]
    assert indices == sorted(indices)
    assert indices[-1] < body.index('id="doctor-decision-form"')
