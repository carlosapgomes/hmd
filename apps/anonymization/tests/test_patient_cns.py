"""CNS estrutural do paciente (slice 001, R1/D1 — RED).

Cobre a gramática de ``extract_patient_cns``: somente linha iniciada por
``CNS:`` no cabeçalho, com/sem espaço após os dois-pontos; valor na MESMA
linha (nunca a seguinte); dígitos com separadores usuais normalizados e
validados por ``valid_cns``; cabeçalhos repetidos com o mesmo CNS válido
deduplicam; dois valores válidos distintos, ou valor não vazio inválido,
deixam a identidade vazia; rótulo vazio não consome o campo seguinte;
narrativa fora do cabeçalho não participa. Cobre ainda o linkage no
``DeterministicExtraction``, a persistência/sobrescrita do wrapper e a
ausência do valor no payload do evento (privacidade).
"""

from __future__ import annotations

import dataclasses

import pytest
from django.test import override_settings

from apps.accounts.models import User
from apps.anonymization import deterministic as det_module
from apps.anonymization.deterministic import (
    DeterministicExtraction,
    run_deterministic_extraction,
)
from apps.anonymization.recognizers import valid_cns
from apps.anonymization.services import anonymize_case_text
from apps.cases.events import CaseEventType
from apps.cases.models import Case

# CNS válidos sintéticos (checksum correto, sem PII real — mesmo método das
# fixtures ``generated_cns`` de apps/anonymization/tests/conftest.py).
CNS_A = "207139200176053"
CNS_B = "605932160152243"
CNS_INVALID = "207139200176054"  # último dígito adulterado: checksum errado.

SYSTEM_ROLE = "system"


def _extract_patient_cns(text: str) -> str | None:
    """Probe da gramática (RED honesto: falha limpa enquanto a API não existe)."""
    extractor = getattr(det_module, "extract_patient_cns", None)
    assert callable(extractor), "extract_patient_cns ainda não existe (RED esperado)"
    result: str | None = extractor(text)
    return result


def _header(cns_line: str) -> str:
    """Cabeçalho SESAB sintético D1 (fix-3: quadro fechado revision002)."""
    return (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        "Paciente:\n"
        "MARIA DA SILVA - Idade: 58 a. - Sexo: F - Raça/Cor: Parda\n"
        "Central Estadual de Regulação\n"
        f"{cns_line}\n"
        "Dias em tela: 3\n"
        "Nome Social:\n"
        "Resumo Clínico: acompanhamento ambulatorial de rotina.\n"
    )


# ── Gramática ─────────────────────────────────────────────────────────────


def test_cns_without_space_after_colon() -> None:
    """R1: ``CNS:<15 dígitos>`` sem espaço tem a mesma gramática do com espaço."""
    assert _extract_patient_cns(_header(f"CNS:{CNS_A}")) == CNS_A
    assert _extract_patient_cns(_header(f"CNS: {CNS_A}")) == CNS_A


def test_repeated_identical_cns_deduplicates() -> None:
    """R1: cabeçalhos de páginas repetidos com o mesmo CNS válido → um valor."""
    text = _header(f"CNS: {CNS_A}") + "\n" + _header(f"CNS:{CNS_A}")
    assert _extract_patient_cns(text) == CNS_A


def test_distinct_valid_cns_leaves_identity_empty() -> None:
    """R1: dois valores válidos distintos → identidade CNS vazia (ambiguidade)."""
    text = _header(f"CNS: {CNS_A}") + "\n" + _header(f"CNS: {CNS_B}")
    assert _extract_patient_cns(text) is None


def test_invalid_nonempty_cns_leaves_identity_empty() -> None:
    """R1: valor não vazio com checksum inválido → identidade CNS vazia."""
    assert valid_cns(CNS_INVALID) is False
    assert _extract_patient_cns(_header(f"CNS: {CNS_INVALID}")) is None


def test_empty_label_does_not_consume_next_field() -> None:
    """R1: rótulo vazio não captura a linha seguinte (``Dias em tela``)."""
    assert _extract_patient_cns(_header("CNS:")) is None
    assert _extract_patient_cns(_header("CNS:   ")) is None


def test_narrative_cns_does_not_identify_patient() -> None:
    """R1: CNS válido na narrativa (fora do rótulo) não serve de identidade."""
    text = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        "Paciente:\n"
        "MARIA DA SILVA - Idade: 58 a. - Sexo: F - Raça/Cor: Parda\n"
        f"Resumo Clínico: paciente com CNS {CNS_A} registrado na unidade.\n"
    )
    assert valid_cns(CNS_A) is True
    assert _extract_patient_cns(text) is None


def test_separated_digits_normalized() -> None:
    """R1: dígitos com separadores usuais normalizam para os 15 dígitos."""
    assert _extract_patient_cns(_header("CNS: 207 1392 0017 6053")) == CNS_A


def test_absent_cns_label_is_empty() -> None:
    """R1: relatório sem rótulo CNS → identidade vazia (sem inferência)."""
    text = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        "Paciente:\n"
        "MARIA DA SILVA - Idade: 58 a. - Sexo: F - Raça/Cor: Parda\n"
    )
    assert _extract_patient_cns(text) is None


def test_run_extraction_carries_patient_cns() -> None:
    """R1: a extração composta carrega o CNS estrutural no linkage."""
    result = run_deterministic_extraction(_header(f"CNS: {CNS_A}"))
    assert isinstance(result, DeterministicExtraction)
    assert getattr(result, "patient_cns", None) == CNS_A
    assert "patient_cns" in {field.name for field in dataclasses.fields(result)}


# ── Wrapper + privacidade ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_wrapper_persists_structural_cns() -> None:
    """R1: o wrapper persiste o CNS do cabeçalho junto da identidade."""
    user = User.objects.create_user(username="cns-persist", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.extracted_text = _header(f"CNS: {CNS_A}") + "Paciente: MARIA DA SILVA\n"
    case.save(update_fields=["extracted_text"])

    with override_settings(ANONYMIZATION_USE_NER=False):
        anonymize_case_text(case)
    case.refresh_from_db()

    assert getattr(case, "patient_cns", "") == CNS_A
    # Tokenizado no corpo (não vaza em claro no texto anonimizado).
    assert CNS_A not in case.anonymized_text


@pytest.mark.django_db
def test_wrapper_overwrites_removed_cns_with_empty() -> None:
    """R1: reprocessamento sem CNS no texto sobrescreve com vazio (sem resto)."""
    user = User.objects.create_user(username="cns-overwrite", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.extracted_text = _header(f"CNS: {CNS_A}") + "Paciente: MARIA DA SILVA\n"
    case.save(update_fields=["extracted_text"])

    with override_settings(ANONYMIZATION_USE_NER=False):
        anonymize_case_text(case)
    case.refresh_from_db()
    assert getattr(case, "patient_cns", "") == CNS_A

    case.extracted_text = _header("CNS:") + "Paciente: MARIA DA SILVA\n"
    case.save(update_fields=["extracted_text"])
    with override_settings(ANONYMIZATION_USE_NER=False):
        anonymize_case_text(case)
    case.refresh_from_db()

    assert getattr(case, "patient_cns", "STALE") == ""


@pytest.mark.django_db
def test_anonymization_event_carries_no_cns_value() -> None:
    """R1/privacidade: o payload do evento não carrega o CNS em claro."""
    user = User.objects.create_user(username="cns-privado", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.extracted_text = _header(f"CNS: {CNS_A}") + "Paciente: MARIA DA SILVA\n"
    case.save(update_fields=["extracted_text"])

    with override_settings(ANONYMIZATION_USE_NER=False):
        anonymize_case_text(case)

    event = case.events.get(event_type=CaseEventType.CASE_ANONYMIZATION_COMPLETED)
    assert CNS_A not in str(event.payload)
