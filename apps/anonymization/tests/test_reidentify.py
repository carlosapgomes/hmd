"""Testes da re-identificação por caso (slice 005, change presidio-anonymization;
design D8/R1; requirement spec "Re-identificação controlada").

Cobre a mecânica fiel: o núcleo puro ``reidentify(texto, mapa)`` — mapa vazio →
texto inalterado e tokens ordenados do maior para o menor (``<PESSOA_1>`` nunca
casa dentro de ``<PESSOA_10>``) — e o roundtrip integral via serviço (R4):
anonimiza um texto sintético com valores fabricados (incluindo um valor que
LITERALMENTE contém um token, ``JOSE <CPF_1> MARIA DA SILVA``) e
``reidentify_text(case, anonymized)`` reproduz o texto original campo a campo
— a passada única de regex não re-substitui em cascata o token literal contido
no valor restaurado. O controle de acesso (quem re-identifica) é dos consumers
(presenter do 07) — fora deste slice.

Como nos testes do serviço (slice 003), o pipeline injeta um analyzer fake no
lugar do singleton (monkeypatch): o modelo real é coberto pelos testes do
benchmark (test_benchmark.py).
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from apps.accounts.models import User
from apps.anonymization.engine import AnonymizationEngine
from apps.anonymization.reidentify import reidentify, reidentify_text
from apps.anonymization.services import anonymize_case_text
from apps.cases.models import Case

SYSTEM_ROLE = "system"


class _StubAnalyzer:
    """Analyzer fake: devolve results pré-configurados a cada chamada."""

    def __init__(self, results: list[SimpleNamespace]) -> None:
        self._results = results

    def analyze(self, **kwargs: object) -> list[SimpleNamespace]:
        del kwargs
        return list(self._results)


def _ner_result(entity_type: str, text: str, value: str) -> SimpleNamespace:
    """Resultado de NER com os offsets exatos de ``value`` dentro de ``text``."""
    start = text.index(value)
    return SimpleNamespace(
        start=start,
        end=start + len(value),
        entity_type=entity_type,
        score=0.9,
    )


def _stub_engine(monkeypatch: pytest.MonkeyPatch, results: list[SimpleNamespace]) -> None:
    """Substitui o singleton do engine por um analyzer fake (sem modelo)."""
    fake = AnonymizationEngine(analyzer=_StubAnalyzer(results), anonymizer=None)
    monkeypatch.setattr("apps.anonymization.services.get_anonymization_engine", lambda: fake)


def _create_case_with_text(text: str) -> Case:
    """Caso com ``extracted_text`` definido para o wrapper do slice 003."""
    user = User.objects.create_user(username="reidentify-teste", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.extracted_text = text
    case.save(update_fields=["extracted_text"])
    return case


# ── R1: núcleo puro sobre (mapa, texto) ──────────────────────────────────


def test_empty_map_unchanged() -> None:
    """R1: mapa vazio → texto inalterado (mesmo com conteúdo que parece token)."""
    text = "Resumo Clínico: acompanhamento do <PESSOA_1> e do <CPF_10>."

    assert reidentify(text, {}) == text


def test_longest_token_first() -> None:
    """R1: tokens ordenados por tamanho — ``<PESSOA_1>`` não casa em ``<PESSOA_10>``.

    O token de prefixo comum (``<PESSOA_1>``) é menor que ``<PESSOA_10>``; a
    alternation com os tokens do maior para o menor restaura cada ocorrência
    pelo valor do token exato.
    """
    pseudonym_map = {
        "<PESSOA_1>": {"value": "MARIA DA SILVA", "entity_type": "PESSOA"},
        "<PESSOA_10>": {"value": "JOSE CARLOS", "entity_type": "PESSOA"},
        "<CPF_1>": {"value": "111.222.333-44", "entity_type": "CPF"},
    }

    result = reidentify(
        "<PESSOA_10> citado antes de <PESSOA_1>, com <CPF_1> anexado.",
        pseudonym_map,
    )

    assert result == "JOSE CARLOS citado antes de MARIA DA SILVA, com 111.222.333-44 anexado."


# ── R4: roundtrip integral via serviço (spec "Re-identificação controlada") ─


@pytest.mark.django_db
def test_roundtrip_full(
    monkeypatch: pytest.MonkeyPatch,
    generated_cpf_formatted: str,
    generated_cns: str,
) -> None:
    """R4/cenário spec: reidentify reproduz integralmente os valores originais.

    O texto contém um nome cujo valor LITERALMENTE contém o token ``<CPF_1>``
    (além de um CPF real que também vira ``<CPF_1>``): a passada única de regex
    substitui cada token pelo valor do mapa sem re-varredura — o ``<CPF_1>``
    literal dentro do nome restaurado NÃO é re-substituído (imune a cascata).
    A comparação campo a campo: ``reidentify_text(case, anonymized) == original``
    e cada valor do mapa reaparece no resultado.
    """
    cpf = generated_cpf_formatted
    name = "JOSE <CPF_1> MARIA DA SILVA"  # valor original que contém literalmente um token
    text = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        f"Paciente: {name}\n"
        "Data de Nascimento: 12/03/1960\n"
        f"CPF: {cpf}\n"
        f"CNS: {generated_cns}\n"
        "Resumo Clínico: paciente encaminhado do Hospital do Coração em Salvador; "
        "avaliação pelo CRM-BA 12345 do corpo clínico.\n"
    )
    _stub_engine(
        monkeypatch,
        [
            _ner_result("ORGANIZATION", text, "Hospital do Coração"),
            _ner_result("LOCATION", text, "Salvador"),
            _ner_result("BR_CRM", text, "CRM-BA 12345"),
        ],
    )
    case = _create_case_with_text(text)

    anonymize_case_text(case)
    case.refresh_from_db()

    # Nada de valor original permanece no texto anonimizado (tokens no lugar).
    anonymized = case.anonymized_text
    for value in (name, cpf, generated_cns, "33345", "12/03/1960", "Hospital do Coração"):
        assert value not in anonymized

    # Roundtrip fiel: o resultado reproduz o texto original integralmente.
    reidentified = reidentify_text(case, anonymized)
    assert reidentified == text

    # Comparação campo a campo: cada token do mapa reaparece com o valor real.
    for entry in case.pseudonym_map.values():
        assert entry["value"] in reidentified

    # A imunidade a cascata: o ``<CPF_1>`` literal do nome restaurado não foi
    # re-substituído pelos dígitos do CPF real (o CPF real aparece uma única vez).
    assert reidentified.count(cpf) == 1
    assert "<CPF_1>" in reidentified
    assert case.patient_birth_date == date(1960, 3, 12)
