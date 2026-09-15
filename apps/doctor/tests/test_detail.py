"""Testes do detalhe do caso médico (doctor-queue-decision, slice 003, R2–R6).

Cobre:
- R2: presenter puro ``build_case_detail_context(case)`` — tokens do mapa do
  caso re-identificados nos 4 artefatos (structured_data/summary_text/
  policy_result/suggested_action), seções da estrutura por chave do schema
  base, alertas da policy (criterion) + sugestão/agregado do LLM2 (motivos),
  requisitos gerais acionáveis derivados (dedupe), prior-case com o MOTIVO
  REAL da row do caso prévio (não o anonimizado) e flag ``can_decide``;
  caso sem artefatos → seções vazias, sem explosão;
- R3: view ``doctor:case_detail`` — 200 para AWAITING_DOCTOR e pós-decisão;
  403 por papel ativo e por subtipo (sem vazar dados no corpo);
- R4: view ``doctor:case_pdf`` — FileResponse 200 application/pdf e 404 sem
  documento na posição; guard igual (papel + subtipo);
- R5/R6: template com cards por seção e nenhum token do mapa do caso na
  renderização (assert escopado; tokens de espaços alheios podem sobrar).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.attachments.models import AttachmentStatus, CaseAttachment, ExtractionMethod, PatientMatch
from apps.cases.models import (
    Case,
    CaseDocument,
    CaseProcedure,
    CaseStatus,
    DetectionStatus,
    DoctorDisposition,
)
from apps.cases.procedures import record_doctor_procedure_decisions
from apps.doctor.presenters import build_case_detail_context

DOCTOR_ROLE = "doctor"
ADMIN_ROLE = "admin"
SYSTEM_ROLE = "system"

ANGIO_TYPE = "art_perif"
CARDIO_TYPE = "cat_cardiaco"

# Mapa do caso (token → valor real), mesmo shape de ``Case.pseudonym_map``.
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "MARIA DA SILVA", "entity_type": "PERSON"},
    "<DATA_1>": {"value": "05/03/1972", "entity_type": "DATE_TIME"},
    "<CPF_1>": {"value": "111.222.333-44", "entity_type": "CPF"},
}


def _create_case_with_declared(created_by: User, procedure_types: Sequence[str]) -> Case:
    """Cria um caso com as rows declaradas (sem transições de status)."""
    case = Case.objects.create(created_by=created_by)
    for procedure_type in procedure_types:
        CaseProcedure.objects.create(
            case=case,
            procedure_type=procedure_type,
            declared_by_nir=True,
        )
    return case


def _advance_to_awaiting_doctor(case: Case) -> None:
    """Dirige o caso pelas transições do pipeline até ``AWAITING_DOCTOR``."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.AWAITING_DOCTOR


def _apply_metadata(
    case: Case,
    *,
    patient_name: str,
    agency_record_number: str,
) -> None:
    """Ajusta os campos de identidade exibidos no detalhe."""
    Case.objects.filter(pk=case.pk).update(
        patient_name=patient_name,
        agency_record_number=agency_record_number,
    )
    case.refresh_from_db()


def _apply_demographics(
    case: Case,
    *,
    age: int | None,
    gender: str,
    race: str,
) -> None:
    """Ajusta a demografia do cabeçalho SESAB exibida no detalhe."""
    Case.objects.filter(pk=case.pk).update(
        patient_age=age,
        patient_gender=gender,
        patient_race=race,
    )
    case.refresh_from_db()


_IDENTIFICATION_CARD_START = "<!-- Identificação do paciente -->"
_IDENTIFICATION_CARD_END = "<!-- Procedimentos do caso -->"
# Ordem de leitura clínica do detalhe (design D2): quadro clínico antes do
# consultivo da automação.
_CLINICAL_ORDER = (
    "Procedimentos do caso",
    "Sumário clínico",
    "Estrutura extraída",
    "Alertas consultivos",
)


def _identification_card(body: str) -> str:
    """Recorte do card de identificação (asserts escopados por linha)."""
    _, card = body.split(_IDENTIFICATION_CARD_START, 1)
    card, _ = card.split(_IDENTIFICATION_CARD_END, 1)
    return card


def _dd_value(card: str, label: str) -> str:
    """Valor do ``<dd>`` que segue o ``<dt>`` do rótulo DENTRO do card."""
    match = re.search(
        rf'<dt class="col-sm-3">{re.escape(label)}</dt>\s*<dd class="col-sm-9">(.*?)</dd>',
        card,
        flags=re.DOTALL,
    )
    assert match is not None, f"linha «{label}» ausente no card de identificação"
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _card_title_index(body: str, title: str) -> int:
    """Índice do título de card no HTML renderizado (ordem dos cards)."""
    marker = f'<h2 class="h5 mb-0">{title}</h2>'
    index = body.find(marker)
    assert index != -1, f"card «{title}» ausente no detalhe"
    return index


def _structured_artifact() -> dict[str, object]:
    """Artefato LLM1 com tokens do mapa do caso em várias seções."""
    return {
        "pedido": {
            "procedimentos_solicitados": [ANGIO_TYPE],
            "evidence_spans": [
                {
                    "field_path": "pedido.procedimentos_solicitados",
                    "excerpt": "Solicitado pelo CRM de <PESSOA_1>.",
                }
            ],
        },
        "contexto_clinico": "Paciente <PESSOA_1> com claudicação intermitente desde <DATA_1>.",
        "linha_do_tempo": [
            {
                "description": "Internação de <PESSOA_1> em <DATA_1>.",
                "status": "confirmado",
                "evidence_spans": [],
            }
        ],
        "exames": {
            "platelets": {
                "status": "confirmado",
                "evidence_spans": [],
                "value": 80000,
                "unit": "/mm³",
            },
            "inr": {"status": "nao_informado", "evidence_spans": [], "value": None, "unit": None},
        },
        "medicacoes": [
            {
                "name": "varfarina",
                "status": "confirmado",
                "evidence_spans": [],
                "drug_class": "anticoagulante",
            }
        ],
        "comorbidades": [],
        "contraindicacoes": [
            {
                "description": "Alergia a contraste relatada por <PESSOA_1>.",
                "status": "confirmado",
                "evidence_spans": [],
            }
        ],
        "trechos_nao_classificados": ["Trecho com <CPF_1> citado."],
    }


def _policy_for(procedure_type: str) -> dict[str, object]:
    """policy_result serializado por procedimento (formato do slice 005)."""
    if procedure_type == ANGIO_TYPE:
        return {
            "procedure_type": ANGIO_TYPE,
            "section_id": "S1",
            "recommendation": "recomenda_recusar",
            "refusal_reasons": ["plaquetas 80000 abaixo do mínimo 100000 da seção S1"],
            "criteria": [
                {
                    "criterion": "platelets",
                    "status": "alerta",
                    "severity": "recusa",
                    "reason": "plaquetas 80000 abaixo do mínimo 100000 da seção S1",
                },
                {
                    "criterion": "anticoagulante",
                    "status": "alerta",
                    "severity": "informativo",
                    "reason": (
                        "anticoagulante varfarina em uso por <PESSOA_1> — "
                        "suspender 7 dias antes do procedimento"
                    ),
                },
            ],
        }
    return {
        "procedure_type": procedure_type,
        "section_id": "S1",
        "recommendation": "recomenda_aceitar",
        "refusal_reasons": [],
        "criteria": [
            {
                "criterion": "anticoagulante",
                "status": "alerta",
                "severity": "informativo",
                "reason": (
                    "anticoagulante varfarina em uso por <PESSOA_1> — "
                    "suspender 7 dias antes do procedimento"
                ),
            }
        ],
    }


def _suggested_action() -> dict[str, object]:
    """suggested_action do LLM2 (chave ``motivos``, pode conter tokens)."""
    return {
        "procedures": {
            ANGIO_TYPE: {
                "suggestion": "recusar",
                "motivos": ["plaquetas abaixo do mínimo", "avaliar <PESSOA_1> com urgência"],
            }
        },
        "aggregate": {
            "suggestion": "recusar",
            "motivos": ["plaquetas abaixo do mínimo", "avaliar <PESSOA_1> com urgência"],
        },
    }


def _make_awaiting_case_with_artifacts(created_by: User) -> Case:
    """Caso em AWAITING_DOCTOR com os 4 artefatos persistidos e mapa populado."""
    case = _create_case_with_declared(created_by, (ANGIO_TYPE,))
    _advance_to_awaiting_doctor(case)
    _apply_metadata(case, patient_name="MARIA DA SILVA", agency_record_number="33345")
    case.pseudonym_map = dict(_PSEUDONYMS)
    case.structured_data = _structured_artifact()
    case.policy_result = {ANGIO_TYPE: _policy_for(ANGIO_TYPE)}
    case.summary_text = "Resumo clínico: paciente <PESSOA_1> com claudicação desde <DATA_1>."
    case.suggested_action = _suggested_action()
    case.save()
    case.refresh_from_db()
    return case


# ── Anexos (slice 004, attachment-processing-ocr): fixtures de cards ───────

# Mapa do ANEXO (namespace estendido do caso, D4): reusa os tokens do caso
# (``<PESSOA_1>``/``<DATA_1>``) e carrega entidades novas só do anexo
# (``<PESSOA_2>``/``<CPF_2>``) — o mapa do anexo é auto-suficiente para
# re-identificar o próprio resumo/evidência.
_ATTACHMENT_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "MARIA DA SILVA", "entity_type": "PERSON"},
    "<DATA_1>": {"value": "05/03/1972", "entity_type": "DATE_TIME"},
    "<PESSOA_2>": {"value": "JOSE DOS SANTOS", "entity_type": "PERSON"},
    "<CPF_2>": {"value": "999.888.777-66", "entity_type": "CPF"},
}

_MATCH_SUMMARY = (
    "Laudo assinado por <PESSOA_2>, paciente <PESSOA_1> nascida em <DATA_1> "
    "com documento <CPF_2> citado na linha 4."
)
_MATCH_EVIDENCE = "Trecho: 'paciente <PESSOA_1>' associado ao <CPF_2> no laudo."


def _add_attachment(
    case: Case,
    uploaded_by: User,
    *,
    filename: str = "laudo-anexo.pdf",
    status: str = AttachmentStatus.PROCESSED,
    extraction_method: str = ExtractionMethod.LOCAL_PDF,
    patient_match: str | None = PatientMatch.MATCH,
    summary: str = "",
    evidence: str = "",
    pseudonym_map: dict[str, dict[str, str]] | None = _ATTACHMENT_PSEUDONYMS,
) -> CaseAttachment:
    """Anexo processado/verificado (evidência suplementar do caso)."""
    attachment = CaseAttachment.objects.create(
        case=case,
        content_type="application/pdf",
        original_filename=filename,
        size_bytes=1024,
        uploaded_by=uploaded_by,
        status=status,
        extraction_method=extraction_method,
        patient_match=patient_match,
        verification_summary=summary,
        verification_evidence=evidence,
    )
    if pseudonym_map is not None:
        attachment.pseudonym_map = dict(pseudonym_map)
        attachment.save(update_fields=["pseudonym_map"])
    return attachment


def _collect_strings(value: Any) -> list[str]:
    """Recolhe recursivamente todas as strings de dict/list do resultado."""
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            strings.extend(_collect_strings(item))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_collect_strings(item))
    return strings


# ── R2: presenter puro ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestBuildCaseDetailContext:
    """R2: presenter re-identifica os 4 artefatos e monta o contexto por seção."""

    def test_reidentifies_four_artifacts(
        self,
        user_factory: Callable[..., User],
    ) -> None:
        """R2: tokens do mapa do caso substituídos em todas as seções/artefatos."""
        owner = user_factory("dono-artefatos", ("nir",))
        case = _make_awaiting_case_with_artifacts(owner)

        context = cast(dict[str, Any], build_case_detail_context(case))

        # Nenhum token DO MAPA DO CASO sobrevive no contexto (assert escopado,
        # D3): as strings re-identificadas carregam os valores reais.
        all_text = _collect_strings(context)
        joined = "\n".join(all_text)
        for token in ("<PESSOA_1>", "<DATA_1>", "<CPF_1>"):
            assert token not in joined
        assert "MARIA DA SILVA" in joined
        assert "111.222.333-44" in joined
        assert "05/03/1972" in joined

        # Summary re-identificado (texto plano, sem tokens).
        assert context["summary_text"] == (
            "Resumo clínico: paciente MARIA DA SILVA com claudicação desde 05/03/1972."
        )

        # Seções da estrutura na ordem do schema base, com linhas por seção.
        section_keys = [section["key"] for section in context["sections"]]
        assert section_keys == [
            "pedido",
            "contexto_clinico",
            "linha_do_tempo",
            "exames",
            "medicacoes",
            "comorbidades",
            "contraindicacoes",
            "trechos_nao_classificados",
        ]
        section_lines = {
            section["key"]: "\n".join(section["lines"]) for section in context["sections"]
        }
        assert "MARIA DA SILVA" in section_lines["contexto_clinico"]
        assert "MARIA DA SILVA" in section_lines["pedido"]
        assert "MARIA DA SILVA" in section_lines["linha_do_tempo"]
        assert "80000" in section_lines["exames"]
        assert "varfarina" in section_lines["medicacoes"]
        assert "MARIA DA SILVA" in section_lines["contraindicacoes"]
        assert "111.222.333-44" in section_lines["trechos_nao_classificados"]

        # Alertas da policy por procedimento (criterion/status/severity/reason)
        # com recomendação e sugestão/agregado do LLM2 (chave motivos).
        assert len(context["advisories"]) == 1
        advisory = context["advisories"][0]
        assert advisory["procedure_type"] == ANGIO_TYPE
        assert advisory["recommendation"] == "recomenda_recusar"
        assert advisory["recommendation_label"] == "Recomenda recusar"
        assert advisory["refusal_reasons"] == [
            "plaquetas 80000 abaixo do mínimo 100000 da seção S1"
        ]
        assert {item["criterion"] for item in advisory["criteria_alerts"]} == {
            "platelets",
            "anticoagulante",
        }
        # Sugestão/agregado do LLM2 usam a chave real ``motivos``.
        assert advisory["suggestion"] == "recusar"
        assert advisory["motivos"] == [
            "plaquetas abaixo do mínimo",
            "avaliar MARIA DA SILVA com urgência",
        ]
        assert context["aggregate"]["suggestion"] == "recusar"
        assert context["aggregate"]["label"] == "Recusar"
        assert "avaliar MARIA DA SILVA com urgência" in context["aggregate"]["motivos"]

        # Requisitos gerais acionáveis derivados dos alertas informativos.
        assert len(context["requirements"]) == 1
        requirement = context["requirements"][0]
        assert requirement["criterion"] == "anticoagulante"
        assert "MARIA DA SILVA" in requirement["reason"]

        # Flag can_decide = estado AWAITING_DOCTOR.
        assert context["can_decide"] is True
        assert context["identification"]["patient_name"] == "MARIA DA SILVA"
        assert context["identification"]["agency_record_number"] == "33345"

    def test_identification_includes_demographics(
        self,
        user_factory: Callable[..., User],
    ) -> None:
        """R1/D1: demografia do cabeçalho SESAB entra na identificação."""
        owner = user_factory("dono-demografia", ("nir",))
        case = _make_awaiting_case_with_artifacts(owner)
        _apply_demographics(case, age=84, gender="F", race="Parda")

        context = cast(dict[str, Any], build_case_detail_context(case))

        identification = cast(dict[str, Any], context["identification"])
        assert identification["patient_age"] == 84
        assert identification["patient_gender"] == "F"
        assert identification["patient_race"] == "Parda"

    def test_requirements_deduplicated_across_procedures(
        self,
        user_factory: Callable[..., User],
    ) -> None:
        """R2: requisitos gerais derivados são deduplicados entre procedimentos."""
        owner = user_factory("dono-dedupe", ("nir",))
        case = _create_case_with_declared(owner, (ANGIO_TYPE, CARDIO_TYPE))
        _advance_to_awaiting_doctor(case)
        # O mesmo alerta informativo (anticoagulante) aparece na policy dos dois.
        case.policy_result = {
            ANGIO_TYPE: _policy_for(ANGIO_TYPE),
            CARDIO_TYPE: _policy_for(CARDIO_TYPE),
        }
        case.save()

        context = cast(dict[str, Any], build_case_detail_context(case))

        assert len(context["advisories"]) == 2
        assert len(context["requirements"]) == 1

    def test_case_without_artifacts_empty_sections(
        self,
        user_factory: Callable[..., User],
    ) -> None:
        """R2: caso sem artefatos não explode — seções vazias e alertas vazios."""
        owner = user_factory("dono-vazio", ("nir",))
        case = _create_case_with_declared(owner, (ANGIO_TYPE,))
        _advance_to_awaiting_doctor(case)
        _apply_metadata(case, patient_name="", agency_record_number="")

        context = cast(dict[str, Any], build_case_detail_context(case))

        assert context["sections"]
        for section in context["sections"]:
            assert section["lines"] == []
        # Card consultivo existe por procedimento declarado, sem alertas.
        assert len(context["advisories"]) == 1
        advisory = context["advisories"][0]
        assert advisory["refusal_reasons"] == []
        assert advisory["criteria_alerts"] == []
        assert advisory["motivos"] == []
        assert context["requirements"] == []
        assert context["aggregate"]["motivos"] == []
        assert context["prior_cases"] == []
        assert context["summary_text"] == ""
        assert context["summary_lines"] == []
        assert context["can_decide"] is True

    def test_can_decide_false_after_decision(
        self,
        user_factory: Callable[..., User],
    ) -> None:
        """R2: flag can_decide = False em caso pós-decisão (leitura)."""
        owner = user_factory("dono-flag", ("nir",))
        doctor = user_factory("medico-flag", (DOCTOR_ROLE,))
        case = _make_awaiting_case_with_artifacts(owner)
        CaseProcedure.objects.filter(case=case).update(
            doctor_disposition=DoctorDisposition.DENIED,
            doctor_reason="INR acima do limite",
            doctor_decided_at=timezone.now(),
        )
        case.record_doctor_decision(accepted=False, user=doctor, role=DOCTOR_ROLE)
        case.refresh_from_db()
        assert case.status == CaseStatus.DOCTOR_DENIED

        context = cast(dict[str, Any], build_case_detail_context(case))

        assert context["can_decide"] is False
        # Disposição atual do procedimento é exibida.
        assert context["declared"][0]["disposition"] == DoctorDisposition.DENIED
        assert context["declared"][0]["disposition_label"] == "Negado"

    def test_prior_case_shows_real_reason(
        self,
        user_factory: Callable[..., User],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """R2/R6: prior-case exibe o MOTIVO REAL da row do caso prévio.

        O resumo do lookup traz o motivo anonimizado (para o LLM2); o card do
        médico re-busca o motivo cru na ``CaseProcedure`` do caso prévio via
        ``prior_case_id`` — D3.
        """

        def _fake_anonymize(
            text: str, seed_map: dict[str, dict[str, str]] | None = None
        ) -> SimpleNamespace:
            del text, seed_map
            return SimpleNamespace(anonymized_text="<ANONIMIZADO>")

        monkeypatch.setattr("apps.pipeline.prior_case.anonymize_text", _fake_anonymize)

        owner = user_factory("dono-prior-card", ("nir",))
        now = timezone.now()
        decided_at = now - timedelta(minutes=5)

        # Caso prévio do mesmo paciente (nº de ocorrência igual), negado.
        prior = _create_case_with_declared(owner, (ANGIO_TYPE,))
        _apply_metadata(prior, patient_name="MARIA DA SILVA", agency_record_number="33345")
        Case.objects.filter(pk=prior.pk).update(created_at=decided_at - timedelta(minutes=5))
        CaseProcedure.objects.filter(case=prior, procedure_type=ANGIO_TYPE).update(
            doctor_disposition=DoctorDisposition.DENIED,
            doctor_reason="Paciente com INR elevado e plaquetas baixas — não liberar.",
            doctor_decided_at=decided_at,
        )

        current = _make_awaiting_case_with_artifacts(owner)
        Case.objects.filter(pk=current.pk).update(agency_record_number="33345")
        current.refresh_from_db()

        context = cast(dict[str, Any], build_case_detail_context(current))

        assert len(context["prior_cases"]) == 1
        prior_card = context["prior_cases"][0]
        assert prior_card["prior_case_id"] == str(prior.case_id)
        assert prior_card["procedure_type"] == ANGIO_TYPE
        assert prior_card["decision_label"] == "Negado"
        # O motivo real (cru) aparece; o anonimizado do resumo não é exibido.
        assert prior_card["reason"] == (
            "Paciente com INR elevado e plaquetas baixas — não liberar."
        )
        assert "<ANONIMIZADO>" not in json.dumps(context, ensure_ascii=False)


# ── R3: view doctor:case_detail ───────────────────────────────────────────


def _login(
    client: Client,
    user: User,
    role: str,
) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


@pytest.mark.django_db
def test_detail_200_awaiting_with_reidentified_cards(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3/R5/R6: detalhe renderiza os cards e não deixa token do mapa no corpo."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    doctor = user_factory("geral-detalhe", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    # Cards por seção (R5).
    for card_title in (
        "Identificação do paciente",
        "Procedimentos do caso",
        "Alertas consultivos",
        "Requisitos gerais acionáveis",
        "Prior-case",
        "Sumário clínico",
        "Estrutura extraída",
        "Documentos",
    ):
        assert card_title in body
    # Conteúdo re-identificado (valores reais no lugar dos tokens).
    assert "MARIA DA SILVA" in body
    assert "33345" in body
    assert "Resumo clínico: paciente MARIA DA SILVA" in body
    assert "Arteriografia periférica" in body
    assert "Recomenda recusar" in body
    assert "plaquetas 80000 abaixo do mínimo" in body
    assert "varfarina" in body
    # Sem prior-case neste cenário → estado vazio do card.
    assert "Sem caso prévio do mesmo procedimento no período." in body
    # Assert escopado: nenhum token do mapa do caso sobrevive à renderização.
    for escaped_token in ("&lt;PESSOA_1&gt;", "&lt;DATA_1&gt;", "&lt;CPF_1&gt;"):
        assert escaped_token not in body


@pytest.mark.django_db
def test_detail_200_decided_case_read_only(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3: caso pós-decisão é aceito no detalhe (read-only; sem formulário)."""
    owner = user_factory("dono-dec", ("nir",))
    doctor = user_factory("medico-dec", (DOCTOR_ROLE,))
    case = _make_awaiting_case_with_artifacts(owner)
    CaseProcedure.objects.filter(case=case).update(
        doctor_disposition=DoctorDisposition.DENIED,
        doctor_reason="INR elevado.",
        doctor_decided_at=timezone.now(),
    )
    case.record_doctor_decision(accepted=False, user=doctor, role=DOCTOR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    viewer = user_factory("geral-dec", (DOCTOR_ROLE,))
    _login(client, viewer, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Negado pelo médico" in body
    assert "Negado" in body  # disposição por procedimento
    # O detalhe é read-only neste slice (sem formulário de decisão).
    assert 'name="decision"' not in body
    assert "Registrar decisão" not in body


@pytest.mark.django_db
def test_detail_renders_patient_demographics(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/D1: idade, sexo e raça/cor no card de identificação."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    _apply_demographics(case, age=84, gender="F", race="Parda")
    doctor = user_factory("geral-demografia", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    card = _identification_card(response.content.decode())
    assert _dd_value(card, "Idade") == "84 a"
    assert _dd_value(card, "Sexo") == "F"
    assert _dd_value(card, "Raça/Cor") == "Parda"


@pytest.mark.django_db
def test_detail_demographics_absent_show_placeholder(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/D1: demografia ausente exibe «—» (assert escopado por linha)."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    _apply_demographics(case, age=None, gender="", race="")
    doctor = user_factory("geral-demografia-vazia", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    card = _identification_card(response.content.decode())
    assert _dd_value(card, "Idade") == "—"
    assert _dd_value(card, "Sexo") == "—"
    assert _dd_value(card, "Raça/Cor") == "—"
    # Os campos existentes do card seguem renderizados.
    assert _dd_value(card, "Paciente") == "MARIA DA SILVA"


@pytest.mark.django_db
def test_detail_clinical_cards_before_advisory(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/D2: quadro clínico antes do consultivo (índices crescentes)."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    doctor = user_factory("geral-ordem", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    indices = [_card_title_index(body, title) for title in _CLINICAL_ORDER]
    assert indices == sorted(indices)


@pytest.mark.django_db
def test_detail_decided_keeps_clinical_order(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/D2: o detalhe decidido (read-only) usa a mesma ordem de leitura."""
    owner = user_factory("dono-ordem-dec", ("nir",))
    doctor = user_factory("medico-ordem-dec", (DOCTOR_ROLE,))
    case = _make_awaiting_case_with_artifacts(owner)
    record_doctor_procedure_decisions(
        case,
        {ANGIO_TYPE: (DoctorDisposition.DENIED, "INR elevado.")},
        user=doctor,
        role=DOCTOR_ROLE,
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.DOCTOR_DENIED
    viewer = user_factory("geral-ordem-dec", (DOCTOR_ROLE,))
    _login(client, viewer, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    indices = [_card_title_index(body, title) for title in _CLINICAL_ORDER]
    assert indices == sorted(indices)
    # Decisões registradas entre o quadro clínico e o consultivo (D2).
    estrutura = _card_title_index(body, "Estrutura extraída")
    decisoes = _card_title_index(body, "Decisões registradas")
    alertas = _card_title_index(body, "Alertas consultivos")
    assert estrutura < decisoes < alertas


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["nir", "scheduler", "manager"])
def test_detail_forbidden_for_non_doctor_roles(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    role: str,
) -> None:
    """R3: papel ativo fora de doctor/admin → 403 sem vazar dados do paciente."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    user = user_factory(f"usuario-detalhe-{role}", (role,))
    _login(client, user, role)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 403
    body = response.content.decode()
    assert "MARIA DA SILVA" not in body
    assert "33345" not in body


@pytest.mark.django_db
def test_detail_forbidden_for_specialist_outside_subtype(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """R3: médico com subtipos fora do caso → 403 (matriz D2, sem vazamento)."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    cardio_doctor = user_factory("doc-cardio-detalhe", (DOCTOR_ROLE,))
    assign_specialties(cardio_doctor, ("cardio",))
    _login(client, cardio_doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 403
    body = response.content.decode()
    assert "MARIA DA SILVA" not in body
    assert "33345" not in body


@pytest.mark.django_db
def test_pdf_forbidden_for_specialist_outside_subtype(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    assign_specialties: Callable[[User, Sequence[str]], None],
) -> None:
    """R4: PDF com o mesmo guard de subtipo do detalhe (matriz D2)."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    document = _add_document(case, nir_user, position=1)
    cardio_doctor = user_factory("doc-cardio-pdf", (DOCTOR_ROLE,))
    assign_specialties(cardio_doctor, ("cardio",))
    _login(client, cardio_doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_pdf", args=[case.case_id, document.position]))

    assert response.status_code == 403


@pytest.mark.django_db
def test_detail_anonymous_redirects_login(client: Client, nir_user: User) -> None:
    """R3: anônimo → redirect ao login (semântica do role_required)."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("login"))


@pytest.mark.django_db
def test_detail_missing_case_404(
    client: Client,
    user_factory: Callable[..., User],
) -> None:
    """R3: caso inexistente → 404 (sem distinção de existência)."""
    import uuid

    doctor = user_factory("geral-404", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[uuid.uuid4()]))

    assert response.status_code == 404


# ── R4: view doctor:case_pdf ──────────────────────────────────────────────


def _add_document(case: Case, uploaded_by: User, *, position: int) -> CaseDocument:
    """Anexa um CaseDocument fake ao caso na posição informada."""
    uploaded = SimpleUploadedFile(
        f"relatorio-{position}.pdf",
        f"%PDF-1.4 conteudo do pdf {position}".encode(),
        content_type="application/pdf",
    )
    return CaseDocument.objects.create(
        case=case,
        file=uploaded,
        position=position,
        original_filename=f"relatorio-sesab-{position}.pdf",
        content_type="application/pdf",
        size_bytes=len(uploaded.read()),
        uploaded_by=uploaded_by,
    )


@pytest.mark.django_db
def test_pdf_served_for_authorized_doctor(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4: PDF do documento por position servido via FileResponse (application/pdf)."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    document = _add_document(case, nir_user, position=1)
    doctor = user_factory("geral-pdf", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_pdf", args=[case.case_id, document.position]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    served_bytes = b"".join(response.streaming_content)  # type: ignore[attr-defined]
    assert served_bytes == b"%PDF-1.4 conteudo do pdf 1"


@pytest.mark.django_db
def test_pdf_404_when_position_missing(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4: posição sem documento no caso → 404."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    _add_document(case, nir_user, position=1)
    doctor = user_factory("geral-pdf-404", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_pdf", args=[case.case_id, 2]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_pdf_forbidden_for_non_doctor_role(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R4: papel ativo fora da matriz → 403 sem servir o PDF."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    document = _add_document(case, nir_user, position=1)
    nir = user_factory("nir-pdf", ("nir",))
    _login(client, nir, "nir")

    response = client.get(reverse("doctor:case_pdf", args=[case.case_id, document.position]))

    assert response.status_code == 403


# ── Anexos na decisão (slice 004, attachment-processing-ocr): R1/R2/R3/R5 ──


@pytest.mark.django_db
class TestAttachmentCards:
    """slice 004: cards de anexo re-identificados no detalhe médico (D5).

    R1/R5: o presenter expõe a seção ``attachments`` — por anexo, nome,
    método legível, status, badge e resumo/evidência RE-IDENTIFICADOS com o
    mapa DO ANEXO (núcleo puro ``reidentify``, sem helper novo); casos sem
    anexo → chave ausente. R2: mismatch é alerta consultivo — sem nenhuma
    ação automática (nada de descarte/bloqueio). R3: tokens jamais
    renderizados.
    """

    def test_presenter_attachments_reidentified(
        self,
        user_factory: Callable[..., User],
    ) -> None:
        """R1/R5: card match re-identificado com o mapa do anexo (sem tokens)."""
        owner = user_factory("dono-card-match", ("nir",))
        case = _make_awaiting_case_with_artifacts(owner)
        _add_attachment(
            case,
            owner,
            filename="laudo-card.pdf",
            summary=_MATCH_SUMMARY,
            evidence=_MATCH_EVIDENCE,
        )

        context = cast(dict[str, Any], build_case_detail_context(case))

        assert "attachments" in context
        cards = context["attachments"]
        assert len(cards) == 1
        card = cast(dict[str, Any], cards[0])
        assert card["original_filename"] == "laudo-card.pdf"
        assert card["method_label"] == "Local"
        assert card["status_label"] == "Processado"
        assert card["badge"] == "match"
        # Resumo/evidência re-identificados (valores reais, zero tokens).
        assert card["summary"] == (
            "Laudo assinado por JOSE DOS SANTOS, paciente MARIA DA SILVA nascida em "
            "05/03/1972 com documento 999.888.777-66 citado na linha 4."
        )
        assert card["evidence"] == (
            "Trecho: 'paciente MARIA DA SILVA' associado ao 999.888.777-66 no laudo."
        )
        # Nenhum token (do caso ou do anexo) sobrevive no contexto.
        joined = "\n".join(_collect_strings(context))
        for token in ("<PESSOA_1>", "<PESSOA_2>", "<CPF_2>", "<DATA_1>"):
            assert token not in joined
        assert "JOSE DOS SANTOS" in joined

    def test_presenter_no_attachments_absent(
        self,
        user_factory: Callable[..., User],
    ) -> None:
        """R1: caso sem anexos → chave ``attachments`` ausente no contexto."""
        owner = user_factory("dono-sem-anexos", ("nir",))
        case = _make_awaiting_case_with_artifacts(owner)

        context = cast(dict[str, Any], build_case_detail_context(case))

        assert "attachments" not in context

    def test_presenter_attachment_status_without_summary(
        self,
        user_factory: Callable[..., User],
    ) -> None:
        """R1/R5: anexos pending/failed → card de status, SEM resumo/evidência."""
        owner = user_factory("dono-status-anexos", ("nir",))
        case = _make_awaiting_case_with_artifacts(owner)
        _add_attachment(
            case,
            owner,
            filename="ecg-pendente.jpg",
            status=AttachmentStatus.PENDING,
            extraction_method="",
            patient_match=None,
        )
        _add_attachment(
            case,
            owner,
            filename="exame-falhou.jpg",
            status=AttachmentStatus.FAILED,
            extraction_method=ExtractionMethod.VISION,
            patient_match=None,
        )

        context = cast(dict[str, Any], build_case_detail_context(case))
        cards = {card["original_filename"]: card for card in context["attachments"]}

        pending_card = cast(dict[str, Any], cards["ecg-pendente.jpg"])
        assert pending_card["badge"] == "pendente"
        assert pending_card["status_label"] == "Pendente"
        assert pending_card["method_label"] == ""
        assert pending_card["summary"] == ""
        assert pending_card["evidence"] == ""
        failed_card = cast(dict[str, Any], cards["exame-falhou.jpg"])
        assert failed_card["badge"] == "falhou"
        assert failed_card["status_label"] == "Falhou"
        assert failed_card["method_label"] == "OCR externo"
        assert failed_card["summary"] == ""
        assert failed_card["evidence"] == ""

    def test_detail_card_mismatch_alert_no_action(
        self,
        client: Client,
        nir_user: User,
        user_factory: Callable[..., User],
    ) -> None:
        """R2/R5: mismatch é alerta consultivo (danger + texto fixo), sem ação."""
        case = _make_awaiting_case_with_artifacts(nir_user)
        _add_attachment(
            case,
            nir_user,
            filename="laudo-divergente.pdf",
            patient_match=PatientMatch.MISMATCH,
            summary=_MATCH_SUMMARY,
        )
        doctor = user_factory("medico-mismatch", (DOCTOR_ROLE,))
        _login(client, doctor, DOCTOR_ROLE)

        response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

        assert response.status_code == 200
        body = response.content.decode()
        assert "Anexos" in body
        assert "laudo-divergente.pdf" in body
        # Alerta fixo do mismatch e conteúdo re-identificado.
        assert "Divergência de identificação — avalie o documento" in body
        assert "JOSE DOS SANTOS" in body
        # Consultivo: nenhum botão/ação automática de descarte/bloqueio.
        assert "Descartar" not in body
        assert "Bloquear" not in body
        assert 'name="attachment' not in body

    def test_detail_no_tokens_rendered(
        self,
        client: Client,
        nir_user: User,
        user_factory: Callable[..., User],
    ) -> None:
        """R3: página final sem tokens do anexo — valores reais no corpo."""
        case = _make_awaiting_case_with_artifacts(nir_user)
        _add_attachment(
            case,
            nir_user,
            filename="laudo-tokens.pdf",
            summary=_MATCH_SUMMARY,
            evidence=_MATCH_EVIDENCE,
        )
        doctor = user_factory("medico-tokens", (DOCTOR_ROLE,))
        _login(client, doctor, DOCTOR_ROLE)

        response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

        assert response.status_code == 200
        body = response.content.decode()
        # Tokens (do caso e do anexo) jamais renderizados — nem escapados.
        for token in ("<PESSOA_1>", "<PESSOA_2>", "<CPF_2>", "<DATA_1>"):
            escaped = token.replace("<", "&lt;").replace(">", "&gt;")
            assert token not in body
            assert escaped not in body
        # Valores reais presentes na página (anexo + artefatos do caso).
        assert "MARIA DA SILVA" in body
        assert "JOSE DOS SANTOS" in body
        assert "999.888.777-66" in body
        assert "05/03/1972" in body

    def test_detail_without_attachments_no_section(
        self,
        client: Client,
        nir_user: User,
        user_factory: Callable[..., User],
    ) -> None:
        """R5: caso sem anexos → página sem a seção (visualmente idêntica)."""
        case = _make_awaiting_case_with_artifacts(nir_user)
        doctor = user_factory("medico-sem-anexos", (DOCTOR_ROLE,))
        _login(client, doctor, DOCTOR_ROLE)

        response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

        assert response.status_code == 200
        body = response.content.decode()
        assert "Anexos" not in body


@pytest.mark.django_db
def test_detail_page_renders_status_and_nonprocessed_badges(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2 (P2 review F2): o card processado exibe o status_label ("Processado")
    no header; os estados pendente/falhou aparecem na página com suas badges,
    sem resumo de verificação."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    _add_attachment(
        case,
        nir_user,
        filename="laudo-ok.pdf",
        summary=_MATCH_SUMMARY,
        evidence=_MATCH_EVIDENCE,
    )
    _add_attachment(
        case,
        nir_user,
        filename="ecg-pendente.jpg",
        status=AttachmentStatus.PENDING,
        extraction_method="",
        patient_match=None,
    )
    _add_attachment(
        case,
        nir_user,
        filename="exame-falhou.jpg",
        status=AttachmentStatus.FAILED,
        extraction_method=ExtractionMethod.VISION,
        patient_match=None,
    )
    doctor = user_factory("medico-status-badges", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    # Status discreto no header de TODOS os cards (inclusive o processado).
    assert body.count("Processado") == 1
    assert "Pendente" in body
    assert "Falhou" in body
    # Badges de resultado na página.
    assert "Coincidente" in body
    # Pendente/falhou sem resumo de verificação (só o processado tem).
    assert body.count("Laudo assinado por") == 1


# ── Slice 003 (painel-ats-parity): trilha sai do detalhe; badge de falha ───


@pytest.mark.django_db
def test_detail_has_no_event_trail(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R3/D4: a trilha de eventos saiu do detalhe médico (vive no painel) e
    nenhum rótulo de evento aparece no lugar dela."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    doctor = user_factory("geral-sem-trilha", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Trilha de eventos" not in body
    # Rótulos de evento do caso (transições + declaração) não são renderizados.
    assert "Caso encaminhado para avaliação médica" not in body
    assert "Procedimentos declarados pelo NIR" not in body


@pytest.mark.django_db
def test_detail_failed_case_shows_error_badge(
    client: Client,
    user_factory: Callable[..., User],
) -> None:
    """R3/D4: caso ``FAILED`` acessível exibe badge de erro (sem trilha/motivo)."""
    creator = user_factory("nir-caso-falho", ("nir",))
    case = _create_case_with_declared(creator, (ANGIO_TYPE,))
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.fail_processing(reason="falha na extração do PDF", user=None, role=SYSTEM_ROLE)
    doctor = user_factory("geral-caso-falho", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))
    body = response.content.decode()

    assert response.status_code == 200
    assert case.status == CaseStatus.FAILED
    assert re.search(
        r'<span class="badge rounded-pill text-bg-danger[^"]*">Falha no processamento</span>',
        body,
    )
    assert "Trilha de eventos" not in body


# ── detected-procedures-visible (slice 002): origem × detecção no card ─────

_PROCEDURES_CARD_START = "<!-- Procedimentos do caso -->"
_PROCEDURES_CARD_END = "<!-- Sumário clínico -->"
_ANGIO_TYPE = "angio_art_perif"


def _procedures_card(body: str) -> str:
    """Recorte do card de procedimentos do caso (asserts escopados por card)."""
    _, card = body.split(_PROCEDURES_CARD_START, 1)
    card, _ = card.split(_PROCEDURES_CARD_END, 1)
    return card


@pytest.mark.django_db
def test_presenter_procedure_rows_include_origin_and_detection(
    user_factory: Callable[..., User],
) -> None:
    """R1/D1: contexto ``declared`` traz TODAS as rows com origem e detecção.

    Declarada (canônica, primeiro) + detectada não-declarada que sobreviveu ao
    bypass; a detecção ``pending`` (caso sem reconciliação) é exposta crua para
    o template omitir o badge.
    """
    owner = user_factory("dono-rows-origem", ("nir",))
    case = _create_case_with_declared(owner, (_ANGIO_TYPE,))
    _advance_to_awaiting_doctor(case)
    CaseProcedure.objects.filter(case=case).update(
        detection_status=DetectionStatus.NOT_DETECTED,
    )
    CaseProcedure.objects.create(
        case=case,
        procedure_type=ANGIO_TYPE,
        declared_by_nir=False,
        detection_status=DetectionStatus.DETECTED,
    )

    context = cast(dict[str, Any], build_case_detail_context(case))

    rows = cast(list[dict[str, Any]], context["declared"])
    assert [row["procedure_type"] for row in rows] == [_ANGIO_TYPE, ANGIO_TYPE]
    assert rows[0]["is_declared"] is True
    assert rows[0]["detection"] == DetectionStatus.NOT_DETECTED
    assert rows[1]["is_declared"] is False
    assert rows[1]["detection"] == DetectionStatus.DETECTED


@pytest.mark.django_db
def test_detail_procedure_card_shows_declared_and_detected(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
) -> None:
    """R2/D1: row coincidente exibe «Declarado» + «Detectado» + disposição."""
    case = _make_awaiting_case_with_artifacts(nir_user)
    CaseProcedure.objects.filter(case=case).update(
        detection_status=DetectionStatus.DETECTED,
    )
    doctor = user_factory("geral-origem-detectada", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))
    body = response.content.decode()

    assert response.status_code == 200
    card = _procedures_card(body)
    assert "Arteriografia periférica" in card
    assert re.search(r'class="badge[^"]*">Declarado</span>', card)
    assert re.search(r'class="badge[^"]*">Detectado</span>', card)
    assert "Disposição: Pendente" in card


@pytest.mark.django_db
def test_detail_procedure_card_shows_detected_without_declaration(
    client: Client,
    user_factory: Callable[..., User],
) -> None:
    """R2/D1: detectada não-declarada (bypass) visível com a origem própria."""
    owner = user_factory("dono-bypass-rows", ("nir",))
    case = _create_case_with_declared(owner, (_ANGIO_TYPE,))
    _advance_to_awaiting_doctor(case)
    CaseProcedure.objects.filter(case=case).update(
        detection_status=DetectionStatus.NOT_DETECTED,
    )
    CaseProcedure.objects.create(
        case=case,
        procedure_type=ANGIO_TYPE,
        declared_by_nir=False,
        detection_status=DetectionStatus.DETECTED,
    )
    doctor = user_factory("geral-origem-bypass", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    response = client.get(reverse("doctor:case_detail", args=[case.case_id]))
    body = response.content.decode()

    assert response.status_code == 200
    card = _procedures_card(body)
    assert "Angioplastia arterial periférica (membros)" in card
    assert "Não detectado" in card
    assert "Arteriografia periférica" in card
    assert "Detectado na extração" in card


@pytest.mark.django_db
def test_detail_procedure_extra_row_shows_detection_badge(
    client: Client,
    user_factory: Callable[..., User],
) -> None:
    """R2/D1 (review P1): a row detectada não-declarada também carrega o badge
    de detecção («Detectado») — origem E detecção para toda row reconciliada."""
    owner = user_factory("dono-bypass-badge", ("nir",))
    case = _create_case_with_declared(owner, (_ANGIO_TYPE,))
    _advance_to_awaiting_doctor(case)
    CaseProcedure.objects.filter(case=case).update(
        detection_status=DetectionStatus.NOT_DETECTED,
    )
    CaseProcedure.objects.create(
        case=case,
        procedure_type=ANGIO_TYPE,
        declared_by_nir=False,
        detection_status=DetectionStatus.DETECTED,
    )
    doctor = user_factory("geral-badge-extra", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:case_detail", args=[case.case_id])).content.decode()
    card = _procedures_card(body)

    assert "Detectado na extração" in card
    assert 'text-bg-success">Detectado</span>' in card


@pytest.mark.django_db
def test_detail_procedure_row_pending_has_no_detection_badge(
    client: Client,
    user_factory: Callable[..., User],
) -> None:
    """R2/D1 (review P2): row ainda não reconciliada ('pending') exibe apenas
    a origem — nenhum badge de detecção no card."""
    owner = user_factory("dono-pending-badge", ("nir",))
    case = _create_case_with_declared(owner, (_ANGIO_TYPE,))
    _advance_to_awaiting_doctor(case)
    CaseProcedure.objects.filter(case=case).update(
        detection_status=DetectionStatus.PENDING,
    )
    doctor = user_factory("geral-badge-pending", (DOCTOR_ROLE,))
    _login(client, doctor, DOCTOR_ROLE)

    body = client.get(reverse("doctor:case_detail", args=[case.case_id])).content.decode()
    card = _procedures_card(body)

    assert "Declarado" in card
    assert "Detectado" not in card
    assert "Não detectado" not in card
