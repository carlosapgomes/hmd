"""Testes do nome do paciente do cabeçalho SESAB real e sua tokenização
(change sesab-header-extraction, slice 002; R1–R4, design D1/D2/D5/D7).

O layout real (extração linear do PyMuPDF) traz o nome completo na linha de
DEMOGRAFIA, antes do rótulo ``Paciente:`` sozinho, com o bloco de cabeçalho
repetido a cada página. Estes testes cobrem: todas as ocorrências do nome
tokenizadas com o MESMO token (mecanismo determinístico existente); o nome
social (quando preenchido na mesma linha do rótulo) com token PRÓPRIO e sem
virar campo do caso (gate 2 do slice); o linkage populado pelo wrapper
``anonymize_case_text`` e o texto anonimizado sem qualquer ocorrência do nome
(varredura folded).

NER DESLIGADO em todos os cenários (``override_settings``): o comportamento
medido é o determinístico-first da fase 2 — o nome do cabeçalho deve ser
tokenizado sem o modelo (o settings de teste liga o NER por default).
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from apps.accounts.models import User
from apps.anonymization.services import (
    _find_folded_occurrences,
    anonymize_case_text,
    anonymize_text,
)
from apps.cases.models import Case

_NER_OFF = override_settings(ANONYMIZATION_USE_NER=False)

# Uma página do cabeçalho padrão SESAB no layout real (valores sintéticos).
_HEADER_PAGE = (
    "RELATÓRIO DE OCORRÊNCIAS\n"
    "5040778\n"
    "ANTONIO CARLOS PEREIRA LIMA - Idade: 79a. - Sexo: F - Raça/Cor: Parda\n"
    "Paciente:\n"
    "Abertura:\n"
    "05/02/2025\n"
    "Código:\n"
    "09:12\n"
    "Governo do Estado da Bahia\n"
    "Secretaria da Saúde do Estado da Bahia\n"
    "Central Estadual de Regulação\n"
    "CNS:547762666886141\n"
    "Dias em tela:\n"
    "5\n"
    "Data Adm. Unid.:\n"
    "05/02/2025\n"
    "Dias Unid.:\n"
    "3\n"
    "Pág: 1\n"
    "09:30:00\n"
    "Data:\n"
    "Hora:\n"
    "Nome Social:\n"
    "Central. Reg.:\n"
    "CER - Central Estadual de Regulação\n"
)

_CLINICAL_BODY = "Resumo Clínico: paciente com dor torácica aos esforços.\n"

_NAME = "ANTONIO CARLOS PEREIRA LIMA"


@_NER_OFF
def test_all_name_occurrences_tokenized_across_pages() -> None:
    """R1/R3: 3 páginas → MESMO ``<PESSOA_1>`` em todas; nenhum vestígio do nome."""
    text = _HEADER_PAGE * 3 + _CLINICAL_BODY

    result = anonymize_text(text)

    assert result.anonymized_text.count("<PESSOA_1>") == 3
    assert _find_folded_occurrences(result.anonymized_text, _NAME) == []
    assert "<PESSOA_2>" not in result.anonymized_text
    assert result.pseudonym_map["<PESSOA_1>"] == {"value": _NAME, "entity_type": "PESSOA"}
    assert result.pseudonym_map["<CNS_1>"]["value"] == "547762666886141"


@_NER_OFF
def test_social_name_gets_own_token() -> None:
    """R2/R3: nome social (mesma linha) recebe token PRÓPRIO, distinto do civil."""
    text = (
        "ANTONIO CARLOS PEREIRA LIMA - Idade: 79a. - Sexo: F - Raça/Cor: Parda\n"
        "Paciente:\n"
        "Nome Social: TONI LIMA\n"
        "Resumo Clínico: acompanhamento ambulatorial.\n"
    )

    result = anonymize_text(text)

    assert result.anonymized_text.count("<PESSOA_1>") == 1
    assert result.anonymized_text.count("<PESSOA_2>") == 1
    assert result.pseudonym_map["<PESSOA_1>"]["value"] == _NAME
    assert result.pseudonym_map["<PESSOA_2>"]["value"] == "TONI LIMA"
    assert _find_folded_occurrences(result.anonymized_text, _NAME) == []
    assert _find_folded_occurrences(result.anonymized_text, "TONI LIMA") == []


@_NER_OFF
def test_social_name_absent_does_not_create_phantom_candidate() -> None:
    """R2 adversarial: rótulo ``Nome Social:`` vazio → nenhum token extra."""
    text = _HEADER_PAGE + _CLINICAL_BODY

    result = anonymize_text(text)

    assert sorted(result.pseudonym_map) == ["<CNS_1>", "<OCORRENCIA_1>", "<PESSOA_1>"]


@_NER_OFF
def test_orphan_clinical_demographics_not_tokenized_as_pessoa() -> None:
    """R1 adversarial (gate 1): «Idade: 79a.» clínico órfão não vira nome/token.

    Menção clínica de idade SEM os outros marcadores na mesma linha, mesmo
    seguida de uma linha ``Paciente:``, não produz candidato PESSOA algum — o
    texto passa intacto ao perímetro determinístico.
    """
    text = (
        "Resumo Clínico: paciente em investigação. "
        "Idade: 79a. conforme anotação do plantão.\n"
        "Paciente:\n"
    )

    result = anonymize_text(text)

    assert result.anonymized_text == text
    assert result.pseudonym_map == {}


@pytest.mark.django_db
@_NER_OFF
def test_wrapper_persists_linkage_from_real_layout() -> None:
    """R3: ``anonymize_case_text`` popula o linkage do layout real (sem nascimento)."""
    user = User.objects.create_user(username="anon-header", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.extracted_text = _HEADER_PAGE * 3 + _CLINICAL_BODY
    case.save(update_fields=["extracted_text"])

    result = anonymize_case_text(case)
    case.refresh_from_db()

    assert case.patient_name == _NAME
    assert case.patient_birth_date is None
    assert case.agency_record_number == "5040778"
    assert case.anonymized_text == result.anonymized_text
    assert _find_folded_occurrences(case.anonymized_text, _NAME) == []


@pytest.mark.django_db
@_NER_OFF
def test_social_name_not_persisted_in_case_fields() -> None:
    """R2/gate 2: o nome social NÃO é escrito em nenhum campo do caso.

    Ele vira candidato PESSOA (tokenizado no texto anonimizado), mas o linkage
    ``patient_name`` continua sendo o nome CIVIL do cabeçalho — writer único
    (``anonymize_case_text``) e fonte única (âncora do cabeçalho).
    """
    user = User.objects.create_user(username="anon-social", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.extracted_text = (
        "ANTONIO CARLOS PEREIRA LIMA - Idade: 79a. - Sexo: F - Raça/Cor: Parda\n"
        "Paciente:\n"
        "Nome Social: TONI LIMA\n" + _CLINICAL_BODY
    )
    case.save(update_fields=["extracted_text"])

    anonymize_case_text(case)
    case.refresh_from_db()

    assert case.patient_name == _NAME
    assert "<PESSOA_2>" in case.anonymized_text
    assert case.pseudonym_map["<PESSOA_2>"]["value"] == "TONI LIMA"
    for value in (case.patient_name, case.anonymized_text, case.summary_text):
        assert _find_folded_occurrences(value, "TONI LIMA") == []
