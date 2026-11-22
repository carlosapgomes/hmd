"""Jornada HTTP da confirmação NIR do procedimento (slice 002, R1–R4).

Cobre, ponta a ponta e sem mockar a confirmação: criação pela primitiva real
(marca prospectiva), divergência real via fake LLM capturável, detalhe com o
card de confirmação, POST manter/trocar com reprocessamento real pelo
disparo pós-commit, e os negativos endurecidos sem mutação (CSRF, papel,
posse, revisão obsoleta, fonte alterada/ilegível, lock ativo, bypass legado
recusado no marcado e preservado no histórico, cardinalidade de campo
repetido, escaping e fallback sem JS).

Os dois testes de jornada usam ``transaction=True`` para provar a cadeia
pós-commit real (serviço → signal on_commit → worker inline até a nova
análise); os negativos usam o rollback padrão e provam ausência de mutação.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from html.parser import HTMLParser
from io import StringIO
from typing import Any

import pymupdf
import pytest
from django.core.management import call_command
from django.db import connection
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.locks import claim_case_lock
from apps.cases.models import (
    PROCEDURE_DIVERGENCE_REASON,
    ActorType,
    Case,
    CaseDocument,
    CaseEvent,
    CaseStatus,
)
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.cases.procedure_review import (
    latest_review_event,
    source_fingerprint_for_case,
)
from apps.intake.services import create_case_with_documents

pytestmark = pytest.mark.django_db(transaction=True)

NIR_ROLE = "nir"


@pytest.fixture(autouse=True)
def _no_llm_inline_by_default() -> Any:
    """Pipeline parado em LLM_EXTRACTING por padrão (transações reais disparam on_commit).

    Sob ``transaction=True`` os callbacks pós-commit disparam de verdade: sem
    este guard, transições manuais até LLM_EXTRACTING enfileirariam o pipeline
    com a factory real (sem chave → FAILED). Os testes que exercem a cadeia
    reabilitam o inline com o fake no próprio escopo."""
    with override_settings(LLM_RUN_TASKS_INLINE=False):
        yield


DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
DECLARED_TYPE = "art_perif"
DETECTED_TYPE = "cat_cardiaco"
SWITCH_TARGET = "flebografia"
FAKE_MODEL_LLM1 = "modelo-llm1-teste"
FAKE_MODEL_LLM2 = "modelo-llm2-teste"
CATALOG_SIZE = 13

_ANONYMIZED_TEXT = (
    "Relatorio de <PESSOA_1>, 58 anos, com claudicacao intermitente. "
    "Solicitada avaliacao hemodinamica."
)
_PSEUDONYMS: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "FULANO DE TAL SILVA", "entity_type": "PERSON"},
}

_REPORT_HEADER = "RELATÓRIO DE OCORRÊNCIAS"
_INSTITUTIONAL_SIGNALS = [
    "Central Estadual de Regulação",
    "Secretaria da Saúde do Estado",
    "Governo do Estado da Bahia",
]
# Seções operacionais SEMPRE com cobertura total da anonimização (R4): "Unid.
# Origem"/"Data Adm. Unid." geram entidades LOCAL parciais ("Unid" vaza no
# texto anonimizado e o guard de tokens da LLM2 recusa o payload) — o gate
# exige ≥3 seções e estas 6 bastam, sem nomes de unidade/hospital.
_SECTIONS = [
    "Código: 33345",
    "Abertura: 01/02/2025",
    "Motivo da Solicitação: cateterismo cardíaco diagnóstico",
    "Complemento da Solicitação: paciente encaminhado para avaliação hemodinâmica",
    "Resumo Clínico: paciente de 58 anos com dor torácica em investigação",
    "FULANO DE TAL SILVA - Idade: 58a. - Sexo Masculino - Raça/Cor Parda",
    "Paciente:",
    "Dias em tela: 3",
]
_PADDING = "Linha de preenchimento para atingir o tamanho mínimo do relatório de regulação."


class ScriptedLlmClient:
    """Fake capturável: substitui só o transporte; serviços/sinais reais."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, list[dict[str, str]], dict[str, Any] | None]] = []

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append((model, list(messages), json_schema))
        if not self.responses:
            raise AssertionError("cliente fake sem respostas agendadas")
        return self.responses.pop(0)


def _artifact(procedure_types: list[str]) -> dict[str, object]:
    return {
        "pedido": {
            "procedimentos_solicitados": procedure_types,
            "evidence_spans": [
                {
                    "field_path": "pedido.procedimentos_solicitados",
                    "excerpt": "O relatorio solicita os procedimentos listados acima.",
                }
            ],
        },
        "contexto_clinico": "Paciente <PESSOA_1> com claudicacao intermitente ha tres meses.",
        "linha_do_tempo": [],
        "exames": None,
        "medicacoes": [],
        "comorbidades": [],
        "contraindicacoes": [],
        "trechos_nao_classificados": [],
    }


def _llm2_response(procedure_types: list[str]) -> dict[str, object]:
    return {
        "summary_text": "Sumario clinico em portugues para o procedimento confirmado.",
        "procedures": [
            {
                "procedure_type": procedure_type,
                "suggestion": "aceitar",
                "motivos": ["sem contraindicoes identificadas"],
                "evidence_spans": [],
            }
            for procedure_type in procedure_types
        ],
    }


def _pdf_bytes(lines: list[str]) -> bytes:
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        document.new_page().insert_text((72, 72), "\n".join(lines))
        data = document.tobytes()  # type: ignore[no-untyped-call]
        return bytes(data)
    finally:
        document.close()  # type: ignore[no-untyped-call]


def _standard_report_lines() -> list[str]:
    lines = [_REPORT_HEADER, *_INSTITUTIONAL_SIGNALS, *_SECTIONS]
    while len("\n".join(lines)) < 560:
        lines.append(_PADDING)
    return lines


def _uploaded(name: str = "relatorio.pdf") -> Any:
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(
        name, _pdf_bytes(_standard_report_lines()), content_type="application/pdf"
    )


class _ReviewCardParser(HTMLParser):
    """Parser mínimo do card de revisão: select, opções e inputs do form."""

    def __init__(self) -> None:
        super().__init__()
        self.select_ids: list[str] = []
        self.options: list[tuple[str, bool]] = []
        self._in_select = False
        self._current_selected = False
        self._current_value: str | None = None
        self.inputs: list[dict[str, str]] = []
        self.textareas: list[str] = []
        self.buttons: list[str] = []
        self.forms: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        if tag == "form":
            self.forms.append(attrs_dict)
        if tag == "select":
            self._in_select = True
            self.select_ids.append(attrs_dict.get("id", ""))
        if tag == "option" and self._in_select:
            self._current_selected = "selected" in attrs_dict
            self._current_value = attrs_dict.get("value", "")
        if tag == "input":
            self.inputs.append(attrs_dict)
        if tag == "textarea":
            self.textareas.append(attrs_dict.get("name", ""))
        if tag == "button":
            self.buttons.append(attrs_dict.get("type", ""))

    def handle_data(self, data: str) -> None:
        if self._in_select and self._current_value is not None:
            self.options.append((self._current_value, self._current_selected))
            self._current_value = None
            self._current_selected = False

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._in_select = False


def _parse(body: str) -> _ReviewCardParser:
    parser = _ReviewCardParser()
    parser.feed(body)
    return parser


@pytest.fixture
def nir_user(user_factory: Callable[[str, str], User]) -> User:
    return user_factory("nir-revisao", NIR_ROLE)


@pytest.fixture
def _seeded_prompts() -> None:
    call_command("seed_prompts", stdout=StringIO())


def _chain(fake: ScriptedLlmClient) -> Any:
    """Override da cadeia real (inline + fake) para criação e POST."""
    return override_settings(
        LLM_RUN_TASKS_INLINE=True,
        LLM_CLIENT_FACTORY=lambda: fake,
        LLM1_MODEL=FAKE_MODEL_LLM1,
        LLM2_MODEL=FAKE_MODEL_LLM2,
    )


def _retained_case(user: User, fake: ScriptedLlmClient) -> Case:
    """Caso marcado retido por divergência via primitiva + pipeline reais."""
    with _chain(fake):
        case = create_case_with_documents(
            user=user,
            role=NIR_ROLE,
            file=_uploaded(),
            procedure_type=DECLARED_TYPE,
        )
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert case.manual_review_reason == PROCEDURE_DIVERGENCE_REASON
    return case


def _post_confirm(
    client: Client,
    case: Case,
    *,
    procedure_type: str = DECLARED_TYPE,
    justification: str = "Confirmo apos leitura do relatorio principal.",
    acknowledged: bool = True,
    review_event_id: Any = "current",
    fingerprint: Any = "current",
    extra: dict[str, Any] | None = None,
) -> Any:
    if review_event_id == "current":
        review = latest_review_event(case)
        assert review is not None
        review_event_id = str(review.pk)
    if fingerprint == "current":
        fingerprint = source_fingerprint_for_case(case)
        assert fingerprint is not None
    data: dict[str, Any] = {
        "procedure_type": procedure_type,
        "review_justification": justification,
        "review_event_id": review_event_id,
        "source_fingerprint": fingerprint,
    }
    if acknowledged:
        data["review_acknowledged"] = "on"
    if extra:
        data.update(extra)
    return client.post(
        reverse("intake:procedure_review_confirm", args=[case.case_id]),
        data=data,
    )


def _event_types(case: Case) -> list[str]:
    return list(case.events.order_by("id").values_list("event_type", flat=True))


# ── R1: marca prospectiva na primitiva única ───────────────────────────────


def test_creation_emits_review_marker(nir_user: User) -> None:
    """R1: a primitiva única emite ENABLED {version: 1} no mesmo atomic."""
    with override_settings(INTAKE_RUN_TASKS_INLINE=False, ANONYMIZATION_RUN_TASKS_INLINE=False):
        case = create_case_with_documents(
            user=nir_user, role=NIR_ROLE, file=_uploaded(), procedure_type=DECLARED_TYPE
        )
    marker = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED)
    assert marker.payload == {"version": 1}
    assert marker.actor == nir_user
    assert marker.actor_role == NIR_ROLE


def test_batch_marks_every_new_case(nir_user: User) -> None:
    """R1: cada caso do lote recebe a marca; originais históricos não têm marca."""
    from apps.intake.services import submit_report_batch

    legacy = Case.objects.create(created_by=nir_user)
    with override_settings(INTAKE_RUN_TASKS_INLINE=False, ANONYMIZATION_RUN_TASKS_INLINE=False):
        cases, errors = submit_report_batch(
            user=nir_user,
            role=NIR_ROLE,
            files=[_uploaded("a.pdf"), _uploaded("b.pdf")],
            procedure_type=DECLARED_TYPE,
        )
    assert errors == []
    assert len(cases) == 2
    for case in cases:
        assert (
            case.events.filter(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED).count() == 1
        )
    assert not legacy.events.filter(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED).exists()


def test_resubmission_marks_new_case_and_preserves_original(nir_user: User) -> None:
    """R1: reenvio corrigido marca o NOVO caso; o original não ganha marca nova."""
    from apps.intake.services import create_corrected_resubmission

    with override_settings(INTAKE_RUN_TASKS_INLINE=False, ANONYMIZATION_RUN_TASKS_INLINE=False):
        original = create_case_with_documents(
            user=nir_user, role=NIR_ROLE, file=_uploaded(), procedure_type=DECLARED_TYPE
        )
    # Pré-condição de estado (ortogonal ao comportamento sob teste — a marca
    # na criação): fixture direta de status, mesmo padrão de outros módulos
    # (a guarda de completude do slice 001 impediria o drive FSM manual com
    # artefatos sintéticos vazios).
    from apps.cases.models import Case as _Case

    _Case.objects.filter(pk=original.pk).update(status=CaseStatus.CLEANED)
    original.refresh_from_db()
    assert original.status == CaseStatus.CLEANED
    original_events_before = list(
        original.events.order_by("id").values_list("event_type", flat=True)
    )

    with override_settings(INTAKE_RUN_TASKS_INLINE=False, ANONYMIZATION_RUN_TASKS_INLINE=False):
        new_case = create_corrected_resubmission(
            original_case=original,
            user=nir_user,
            role=NIR_ROLE,
            file=_uploaded("corrigido.pdf"),
            procedure_type=DECLARED_TYPE,
            correction_reason="Correção do relatório pelo NIR.",
        )
    assert (
        new_case.events.filter(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED).count() == 1
    )
    # O original recebe SÓ o vínculo de supersessão do próprio reenvio
    # (comportamento existente) — nenhuma marca nova, nenhum dado alterado.
    after = list(original.events.order_by("id").values_list("event_type", flat=True))
    assert after == [*original_events_before, CaseEventType.CASE_MARKED_SUPERSEDED.value]
    assert (
        original.events.filter(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED).count() == 1
    )
    original.refresh_from_db()
    assert original.status == CaseStatus.CLEANED


# ── R1–R3: jornadas manter/trocar com cadeia pós-commit real ───────────────


def test_journey_maintain_procedure_end_to_end(client: Client, nir_user: User) -> None:
    """Manter A com detecção contraditória repetida até a análise confirmada."""
    call_command("seed_prompts", stdout=StringIO())
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact([DETECTED_TYPE])),
            json.dumps(_artifact([DETECTED_TYPE])),
            json.dumps(_llm2_response([DECLARED_TYPE])),
        ]
    )
    with _chain(fake):
        case = create_case_with_documents(
            user=nir_user, role=NIR_ROLE, file=_uploaded(), procedure_type=DECLARED_TYPE
        )
    case.refresh_from_db()
    assert case.manual_review_required is True
    client.force_login(nir_user)

    response = client.get(reverse("intake:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Liberar caso" not in body
    parser = _parse(body)
    assert len(parser.options) == CATALOG_SIZE
    assert [value for value, _ in parser.options] == [
        profile.procedure_type for profile in PROCEDURE_PROFILES
    ]
    selected = [value for value, is_selected in parser.options if is_selected]
    assert selected == [DECLARED_TYPE]
    assert "review_justification" in parser.textareas
    assert any(
        item.get("name") == "review_acknowledged" and item.get("type") == "checkbox"
        for item in parser.inputs
    )
    assert not any(
        item.get("type") == "file" and item.get("name") != "documents" for item in parser.inputs
    )
    assert not any(item.get("type") == "file" for item in parser.inputs)

    with _chain(fake):
        response = _post_confirm(client, case)
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    applied = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED)
    assert applied.payload["effective_procedures"] == [DECLARED_TYPE]
    assert applied.payload["automatic_procedures"] == [DETECTED_TYPE]
    assert CaseEventType.CASE_GATE_BYPASSED.value not in _event_types(case)
    assert len(fake.calls) == 3


def test_journey_switch_to_undetected_end_to_end(client: Client, nir_user: User) -> None:
    """Trocar A→C (não detectado): declarado vira o singleton confirmado."""
    call_command("seed_prompts", stdout=StringIO())
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact([DETECTED_TYPE])),
            json.dumps(_artifact([SWITCH_TARGET])),
            json.dumps(_llm2_response([SWITCH_TARGET])),
        ]
    )
    with _chain(fake):
        case = create_case_with_documents(
            user=nir_user, role=NIR_ROLE, file=_uploaded(), procedure_type=DECLARED_TYPE
        )
    client.force_login(nir_user)
    case.refresh_from_db()
    assert case.manual_review_required is True

    with _chain(fake):
        response = _post_confirm(client, case, procedure_type=SWITCH_TARGET)
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR
    from apps.cases.procedures import get_declared_procedure_types

    assert list(get_declared_procedure_types(case)) == [SWITCH_TARGET]
    applied = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_APPLIED)
    assert applied.payload["effective_procedures"] == [SWITCH_TARGET]


# ── R2: bypass legado recusado no marcado, preservado no histórico ─────────


def test_bypass_refused_for_marked_case(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """R2: POST de liberação simples não resolve divergência de caso novo."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    events_before = _event_types(case)
    client.force_login(nir_user)

    response = client.post(reverse("intake:gate_release", args=[case.case_id]))

    assert response.status_code == 400
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_EXTRACTING
    assert case.manual_review_required is True
    assert _event_types(case) == events_before


def test_legacy_bypass_preserved_for_unmarked_case(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """R2: caso sem marca mantém o contrato antigo (liberação funciona)."""
    with override_settings(INTAKE_RUN_TASKS_INLINE=False, ANONYMIZATION_RUN_TASKS_INLINE=False):
        case = create_case_with_documents(
            user=nir_user, role=NIR_ROLE, file=_uploaded(), procedure_type=DECLARED_TYPE
        )
    CaseEvent.objects.filter(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED
    ).delete()
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.manual_review_required = True
    case.manual_review_reason = PROCEDURE_DIVERGENCE_REASON
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    client.force_login(nir_user)

    response = client.post(reverse("intake:gate_release", args=[case.case_id]))

    assert response.status_code == 302
    case.refresh_from_db()
    assert case.status == CaseStatus.LLM_SUMMARIZING


def test_confirm_refused_for_unmarked_case(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """R2: o POST novo não aceita caso sem marca (400 sem mutação)."""
    with override_settings(INTAKE_RUN_TASKS_INLINE=False, ANONYMIZATION_RUN_TASKS_INLINE=False):
        case = create_case_with_documents(
            user=nir_user, role=NIR_ROLE, file=_uploaded(), procedure_type=DECLARED_TYPE
        )
    CaseEvent.objects.filter(
        case=case, event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED
    ).delete()
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.manual_review_required = True
    case.manual_review_reason = PROCEDURE_DIVERGENCE_REASON
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    events_before = _event_types(case)
    client.force_login(nir_user)

    response = _post_confirm(
        client, case, review_event_id="1", fingerprint="v1:abc", acknowledged=True
    )

    assert response.status_code == 400
    case.refresh_from_db()
    assert case.manual_review_required is True
    assert _event_types(case) == events_before


def test_legacy_empty_body_is_not_confirmation(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """R2: body vazio na rota antiga não confirma caso novo (400, sem efeito)."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    events_before = _event_types(case)
    client.force_login(nir_user)

    response = client.post(reverse("intake:gate_release", args=[case.case_id]), data={})

    assert response.status_code == 400
    case.refresh_from_db()
    assert case.manual_review_required is True
    assert _event_types(case) == events_before


# ── R3/R4: validação do formulário e cardinalidade ─────────────────────────


def test_cardinality_repeated_field_rejected(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """R3: campo repetido (getlist>1) é rejeitado; ChoiceField sozinho não basta."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    events_before = _event_types(case)
    review = latest_review_event(case)
    assert review is not None
    fingerprint = source_fingerprint_for_case(case)
    client.force_login(nir_user)

    # Campo repetido de verdade (doseq): o test client codifica a lista como
    # dois parâmetros — o QueryDict sozinho tomaria só o último.
    response = client.post(
        reverse("intake:procedure_review_confirm", args=[case.case_id]),
        data={
            "procedure_type": [DECLARED_TYPE, SWITCH_TARGET],
            "review_justification": "Justificativa válida.",
            "review_acknowledged": "on",
            "review_event_id": str(review.pk),
            "source_fingerprint": fingerprint or "",
        },
    )

    assert response.status_code == 400
    case.refresh_from_db()
    assert _event_types(case) == events_before
    assert case.manual_review_required is True


@pytest.mark.parametrize(
    "procedure_type,justification,acknowledged",
    [
        ("", "Justificativa válida.", True),
        ("tipo_desconhecido", "Justificativa válida.", True),
        (DECLARED_TYPE, "", True),
        (DECLARED_TYPE, "   ", True),
        (DECLARED_TYPE, "x" * 501, True),
        (DECLARED_TYPE, "com\x00nul", True),
        (DECLARED_TYPE, "Justificativa válida.", False),
    ],
)
def test_invalid_posts_rejected_without_mutation(
    client: Client,
    nir_user: User,
    _seeded_prompts: None,
    procedure_type: str,
    justification: str,
    acknowledged: bool,
) -> None:
    """R3/R4: seleção/consentimento/justificativa inválidos → 400 sem mutação."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    events_before = _event_types(case)
    client.force_login(nir_user)

    response = _post_confirm(
        client,
        case,
        procedure_type=procedure_type,
        justification=justification,
        acknowledged=acknowledged,
    )

    assert response.status_code == 400
    case.refresh_from_db()
    assert case.manual_review_required is True
    assert _event_types(case) == events_before


def test_error_rerender_preserves_escaped_input_without_consent(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """R4: re-render preserva tentativa escapada e nunca marca leitura."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    client.force_login(nir_user)
    payload = '<script>alert("x")</script>'

    response = _post_confirm(client, case, procedure_type="", justification=payload)

    assert response.status_code == 400
    body = response.content.decode()
    assert payload not in body
    assert "&lt;script&gt;" in body
    parser = _parse(body)
    checkboxes = [item for item in parser.inputs if item.get("name") == "review_acknowledged"]
    assert checkboxes
    assert all("checked" not in item for item in checkboxes)


def test_get_creates_nothing(client: Client, nir_user: User, _seeded_prompts: None) -> None:
    """R4: GET do detalhe não cria marker/confirmação/lease/job."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    client.force_login(nir_user)
    events_before = _event_types(case)
    locks_before = case.locked_until
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM django_q_schedule")
        schedules_before = cursor.fetchone()[0]

    response = client.get(reverse("intake:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    case.refresh_from_db()
    assert _event_types(case) == events_before
    assert case.locked_until == locks_before
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM django_q_schedule")
        assert cursor.fetchone()[0] == schedules_before


# ── R4: negativos endurecidos ──────────────────────────────────────────────


def test_stale_review_rejected_without_mutation(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """R4: revisão obsoleta íntegra → 400 orientando reabrir, sem mutação."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    old_review = latest_review_event(case)
    assert old_review is not None
    old_payload = dict(old_review.payload) if isinstance(old_review.payload, dict) else {}
    CaseEvent.objects.create(
        case=case,
        event_type=CaseEventType.CASE_GATE_PROCEDURE_DIVERGENCE,
        actor_type=ActorType.SYSTEM,
        actor=None,
        actor_role=SYSTEM_ROLE,
        payload=old_payload,
    )
    events_before = _event_types(case)
    client.force_login(nir_user)

    response = _post_confirm(client, case, review_event_id=str(old_review.pk))

    assert response.status_code == 400
    assert "reabra" in response.content.decode().lower()
    case.refresh_from_db()
    assert _event_types(case) == events_before
    assert case.manual_review_required is True


def test_changed_bytes_rejected_without_mutation(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """R4: mesmos nomes de PDF com bytes distintos → 400 de suporte, sem mutação."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    document = CaseDocument.objects.get(case=case, position=1)
    name = document.original_filename
    document.file.save(name, _uploaded("outro.pdf").file, save=True)
    events_before = _event_types(case)
    client.force_login(nir_user)

    stale_fingerprint = "v1:" + "0" * 64
    review = latest_review_event(case)
    assert review is not None
    response = client.post(
        reverse("intake:procedure_review_confirm", args=[case.case_id]),
        data={
            "procedure_type": DECLARED_TYPE,
            "review_justification": "Justificativa válida.",
            "review_acknowledged": "on",
            "review_event_id": str(review.pk),
            "source_fingerprint": stale_fingerprint,
        },
    )

    assert response.status_code == 400
    case.refresh_from_db()
    assert _event_types(case) == events_before
    assert case.manual_review_required is True


def test_illegible_source_rejected_without_mutation(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """R4: fonte ilegível/ausente → 400 sem mutação nem novo dispatch."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    document = CaseDocument.objects.get(case=case, position=1)
    document.file.delete(save=False)
    events_before = _event_types(case)
    client.force_login(nir_user)

    # Fingerprint explícito: o servidor computa fonte indisponível (None).
    response = _post_confirm(client, case, fingerprint="v1:" + "0" * 64)

    assert response.status_code == 400
    case.refresh_from_db()
    assert _event_types(case) == events_before
    assert case.manual_review_required is True


def test_active_lock_conflicts_with_409(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """R4: lock ativo de outro ator → 409 sem mutação e sem mensagem técnica."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    other = User.objects.create_user(username="outro-nir", password="senha-teste")
    from apps.accounts.models import Role

    other.roles.add(Role.objects.get_or_create(name=NIR_ROLE)[0])
    claim_case_lock(case, user=other, context="worker_llm", role=SYSTEM_ROLE)
    events_before = _event_types(case)
    client.force_login(nir_user)

    response = _post_confirm(client, case)

    assert response.status_code == 409
    case.refresh_from_db()
    assert _event_types(case) == events_before
    assert case.manual_review_required is True


def test_wrong_role_forbidden(client: Client, nir_user: User, _seeded_prompts: None) -> None:
    """R4: papel ativo não-NIR → 403 sem mutação."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    doctor = User.objects.create_user(username="medico-x", password="senha-teste")
    from apps.accounts.models import Role

    doctor.roles.add(Role.objects.get_or_create(name=DOCTOR_ROLE)[0])
    events_before = _event_types(case)
    client.force_login(doctor)

    response = _post_confirm(client, case)

    assert response.status_code == 403
    case.refresh_from_db()
    assert _event_types(case) == events_before


def test_foreign_case_is_not_found(
    client: Client, nir_user: User, _seeded_prompts: None, user_factory: Any
) -> None:
    """R2 (visibilidade-por-equipe slice 003, corpo invertido — D6: título
    antitético preservado): NIR não-criador confirma o procedimento da equipe —
    POST aceito (302) com o OPERADOR como ator da confirmação."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    other = user_factory("outro-criador", NIR_ROLE)
    client.force_login(other)

    response = _post_confirm(client, case)

    assert response.status_code == 302
    case.refresh_from_db()
    assert case.manual_review_required is False
    confirmation = case.events.get(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED)
    assert confirmation.actor == other
    assert confirmation.actor_role == NIR_ROLE


def test_csrf_enforced_without_mutation(nir_user: User, _seeded_prompts: None) -> None:
    """R4: POST sem token CSRF → 403 sem mutação."""
    fake = ScriptedLlmClient([json.dumps(_artifact([DETECTED_TYPE]))])
    case = _retained_case(nir_user, fake)
    events_before = _event_types(case)
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(nir_user)

    response = _post_confirm(csrf_client, case)

    assert response.status_code == 403
    case.refresh_from_db()
    assert _event_types(case) == events_before


def test_select_renders_thirteen_options_and_posts_without_js(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """R3: select canônico com 13 opções; submit puro (sem JS) confirma."""
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact([DETECTED_TYPE])),
            json.dumps(_artifact([DECLARED_TYPE])),
            json.dumps(_llm2_response([DECLARED_TYPE])),
        ]
    )
    with _chain(fake):
        case = create_case_with_documents(
            user=nir_user, role=NIR_ROLE, file=_uploaded(), procedure_type=DECLARED_TYPE
        )
    case.refresh_from_db()
    assert case.manual_review_required is True
    client.force_login(nir_user)

    response = client.get(reverse("intake:case_detail", args=[case.case_id]))
    assert response.status_code == 200
    parser = _parse(response.content.decode())
    assert len(parser.options) == CATALOG_SIZE

    with _chain(fake):
        response = _post_confirm(client, case)
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.status == CaseStatus.AWAITING_DOCTOR


def test_success_flash_uses_human_reference(
    client: Client, nir_user: User, _seeded_prompts: None
) -> None:
    """Flash de sucesso cita a referência humana, nunca o uid completo."""
    fake = ScriptedLlmClient(
        [
            json.dumps(_artifact([DETECTED_TYPE])),
            json.dumps(_artifact([DECLARED_TYPE])),
            json.dumps(_llm2_response([DECLARED_TYPE])),
        ]
    )
    with _chain(fake):
        case = create_case_with_documents(
            user=nir_user, role=NIR_ROLE, file=_uploaded(), procedure_type=DECLARED_TYPE
        )
    case.refresh_from_db()
    assert case.display_reference == "33345"
    client.force_login(nir_user)

    with _chain(fake):
        response = _post_confirm(client, case, acknowledged=True)

    assert response.status_code == 302
    follow = client.get(response["Location"])
    body = follow.content.decode()
    # Referência humana na mensagem; o uid segue apenas como chave das URLs.
    assert "Caso 33345 confirmado" in body
    assert f"Caso {case.case_id}" not in body
