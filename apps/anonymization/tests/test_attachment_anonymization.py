"""Testes da semeadura do espaço de tokens (change attachment-processing-ocr,
slice 003, R1/D4).

Cobre o contrato do namespace estendido do anexo: ``anonymize_text(text,
seed_map=None)`` (aditivo — sem seed o comportamento é inalterado) e o wrapper
``anonymize_attachment_text(case, text)`` (semeado com ``case.pseudonym_map``):
(a) valor igual ao de uma entidade do caso → MESMO token do caso (paciente do
caso → ``<PESSOA_N>`` do caso, qualquer que seja a ordem no texto — ruído PESSOA
antes não muda o token); (b) paciente DIFERENTE no anexo → token NOVO (número
acima do máximo do caso, distinto do token do caso); (c) regressão do núcleo
sem seed; (d) mapa do caso intocado e mapa do ANEXO apenas com entradas
efetivamente usadas (semeadas usadas + novas).

O engine é sempre um analyzer fake (monkeypatch de
``apps.anonymization.services.get_anonymization_engine``) — sem modelo spaCy
na suíte (padrão do ``test_services.py``).
"""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace

import pytest

from apps.accounts.models import User
from apps.anonymization.engine import AnonymizationEngine
from apps.anonymization.services import anonymize_attachment_text, anonymize_text
from apps.cases.models import Case


class _StubAnalyzer:
    """Analyzer fake: devolve results pré-configurados a cada chamada."""

    def __init__(self, results: Iterable[SimpleNamespace]) -> None:
        self._results = list(results)

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


def _case(*, pseudonym_map: dict[str, dict[str, str]], patient_name: str = "") -> Case:
    """Caso com mapa e linkage persistidos (como pós-anonimização do relatório)."""
    user = User.objects.create_user(username="dono-anexo-anon", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.pseudonym_map = pseudonym_map
    case.patient_name = patient_name
    case.save(update_fields=["pseudonym_map", "patient_name"])
    return case


# Mapa do CASO (relatório principal): o médico assistente é o <PESSOA_1> e o
# paciente do caso é o <PESSOA_2> — o anexo deve convergir para ESTES tokens.
_CASE_MAP: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "JOSE CARLOS DE OLIVEIRA", "entity_type": "PESSOA"},
    "<PESSOA_2>": {"value": "MARIA DA SILVA", "entity_type": "PESSOA"},
}


@pytest.mark.django_db
def test_attachment_seed_reuses_case_tokens_with_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1(a): ruído PESSOA ANTES do nome do paciente no anexo → o paciente do
    caso mantém o token do caso (<PESSOA_2>) e o ruído (fora do mapa) ganha
    token NOVO (<PESSOA_3> — acima do máximo do caso). Sem semeadura, o ruído
    viraria <PESSOA_1> e o paciente <PESSOA_2> por coincidência de ordem; o
    assert do mapa (apenas usados) prova a semeadura."""
    text = "Médico responsável: Dr. Carlos Alberto Pereira\nPaciente: Maria da Silva\n"
    _stub_engine(
        monkeypatch,
        [_ner_result("PERSON", text, "Carlos Alberto Pereira")],
    )
    case = _case(pseudonym_map=dict(_CASE_MAP), patient_name="Maria da Silva")
    original_map = dict(_CASE_MAP)

    result = anonymize_attachment_text(case, text)

    output = result.anonymized_text
    assert "<PESSOA_2>" in output  # paciente do caso mantém o token do caso
    assert "<PESSOA_3>" in output  # ruído: token novo, acima do máx (2)
    assert "<PESSOA_1>" not in output  # seed não usado no texto não entra
    assert "Maria da Silva" not in output
    assert "Carlos Alberto Pereira" not in output

    mapping = result.pseudonym_map
    assert set(mapping) == {"<PESSOA_2>", "<PESSOA_3>"}  # APENAS os usados
    assert mapping["<PESSOA_2>"]["value"] == "Maria da Silva"
    assert mapping["<PESSOA_3>"]["value"] == "Carlos Alberto Pereira"

    # (d) mapa do caso intocado — mesmas entradas e valores.
    assert case.pseudonym_map == original_map
    assert case.pseudonym_map == _CASE_MAP


@pytest.mark.django_db
def test_attachment_different_patient_gets_distinct_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1(b): anexo de paciente DIFERENTE (Ana Beatriz Souza, fora do mapa) →
    token NOVO distinto, numerado acima do máximo do caso (<PESSOA_3>), nunca
    o token do paciente do caso (<PESSOA_2>) — mismatch detectável pelo LLM."""
    text = "Anexo de exame complementar\nPaciente: Ana Beatriz Souza\n"
    _stub_engine(monkeypatch, [])  # nome do anexo cobre só o caminho determinístico
    case = _case(pseudonym_map=dict(_CASE_MAP), patient_name="Maria da Silva")

    result = anonymize_attachment_text(case, text)

    output = result.anonymized_text
    assert "<PESSOA_3>" in output
    assert "<PESSOA_2>" not in output  # paciente do caso NÃO aparece no anexo
    assert "<PESSOA_1>" not in output
    assert "Ana Beatriz Souza" not in output
    mapping = result.pseudonym_map
    assert set(mapping) == {"<PESSOA_3>"}
    assert mapping["<PESSOA_3>"]["value"] == "Ana Beatriz Souza"


@pytest.mark.django_db
def test_attachment_reuses_seeded_token_when_patient_first_in_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1(a): o paciente do caso é a 1ª PESSOA do texto do anexo e ainda assim
    mantém o token do caso (<PESSOA_2>) — numeração do seed, não primeira
    ocorrência do texto (sem semeadura seria <PESSOA_1>)."""
    text = "Paciente: Maria da Silva\nResumo Clínico: ECG ambulatorial em repouso.\n"
    _stub_engine(monkeypatch, [])
    case = _case(pseudonym_map=dict(_CASE_MAP), patient_name="Maria da Silva")

    result = anonymize_attachment_text(case, text)

    output = result.anonymized_text
    assert output.count("<PESSOA_2>") == 1
    assert "<PESSOA_1>" not in output
    assert "<PESSOA_3>" not in output
    assert "ECG ambulatorial em repouso" in output
    assert set(result.pseudonym_map) == {"<PESSOA_2>"}
    assert case.pseudonym_map == _CASE_MAP


@pytest.mark.django_db
def test_anonymize_text_without_seed_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """R1(c): regressão — ``anonymize_text(text)`` sem seed (default ``None``)
    e com ``seed_map={}`` produzem EXATAMENTE o mesmo resultado do núcleo
    (numeração por primeira ocorrência no texto; aditivo não altera fluxo)."""
    text = (
        "Código: 33345\n"
        "Paciente: MARIA DA SILVA SOUZA\n"
        "Resumo Clínico: acompanhamento ambulatorial de rotina.\n"
    )
    _stub_engine(monkeypatch, [])

    plain = anonymize_text(text)
    seeded_empty = anonymize_text(text, seed_map={})

    assert plain.anonymized_text == seeded_empty.anonymized_text
    assert plain.pseudonym_map == seeded_empty.pseudonym_map
    assert "<PESSOA_1>" in plain.anonymized_text
    assert "MARIA DA SILVA SOUZA" not in plain.anonymized_text
    assert plain.pseudonym_map["<PESSOA_1>"] == {
        "value": "MARIA DA SILVA SOUZA",
        "entity_type": "PESSOA",
    }


@pytest.mark.django_db
def test_case_map_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1(d): o wrapper semeado com o mapa do caso devolve o resultado sem
    tocar em ``case.pseudonym_map`` nem no texto/artefatos do caso (a
    persistência da row do anexo é da task)."""
    text = "Paciente: Maria da Silva\n"
    _stub_engine(monkeypatch, [])
    case = _case(pseudonym_map=dict(_CASE_MAP), patient_name="Maria da Silva")
    case.anonymized_text = "relatório do caso (não pode mudar)"
    case.save(update_fields=["anonymized_text"])

    result = anonymize_attachment_text(case, text)

    assert result.anonymized_text == "Paciente: <PESSOA_2>\n"
    case.refresh_from_db()
    assert case.pseudonym_map == _CASE_MAP
    assert case.anonymized_text == "relatório do caso (não pode mudar)"
