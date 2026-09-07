"""Testes do serviço de anonimização (slice 003, change presidio-anonymization;
design D5/D6; R1–R6).

Cobre: o operador de pseudônimos estáveis (R1 — tokens distintos por valor,
repetição = mesmo token), a política determinística completa de merge R2/R3
(casos de sobreposição + span determinístico garantido sem NER + varredura
zero-PII), o núcleo puro ``anonymize_text`` e o wrapper transacional
``anonymize_case_text`` com campos/evento (R4/R5) e o texto vazio defensivo
(R6).

Os testes de pipeline injetam um analyzer fake no lugar do singleton
(monkeypatch em ``apps.anonymization.services.get_anonymization_engine``): o
comportamento de NER/recognizers é controlado com precisão (o modelo real é
coberto pelos testes do engine, ``test_engine.py``) e os testes do serviço
ficam determinísticos e sem carga de modelo.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from types import SimpleNamespace

import pytest

from apps.accounts.models import User
from apps.anonymization.deterministic import (
    extract_cns_candidates,
    extract_cpf_candidates,
)
from apps.anonymization.engine import AnonymizationEngine
from apps.anonymization.recognizers import valid_cns, valid_cpf
from apps.anonymization.services import (
    SpanCandidate,
    anonymize_case_text,
    anonymize_text,
    merge_span_candidates,
)
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseStatus

SYSTEM_ROLE = "system"


# ── Analyzer fake (sem modelo spaCy) ──────────────────────────────────────


class _StubAnalyzer:
    """Analyzer fake: devolve results pré-configurados a cada chamada."""

    def __init__(self, results: Iterable[SimpleNamespace]) -> None:
        self._results = list(results)

    def analyze(self, **kwargs: object) -> list[SimpleNamespace]:
        del kwargs
        return list(self._results)


class _ExplodingAnalyzer:
    """Analyzer fake que sempre propaga exceção (teste de rollback)."""

    def analyze(self, **kwargs: object) -> list[SimpleNamespace]:
        del kwargs
        raise RuntimeError("falha simulada do analyzer")


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


# ── R1: tokens estáveis ───────────────────────────────────────────────────


def test_stable_tokens_distinct_values(
    monkeypatch: pytest.MonkeyPatch, generated_cpf_formatted: str
) -> None:
    """R1: valores distintos → tokens distintos, numerados por primeira ocorrência.

    Dois nomes (o primeiro determinístico do rótulo, o segundo só via NER) e um
    CPF válido repetido no corpo: o primeiro nome mencionado vira ``<PESSOA_1>``,
    o segundo ``<PESSOA_2>`` e o CPF repetido usa SEMPRE ``<CPF_1>`` (mesmo
    token nas duas ocorrências).
    """
    text = (
        "Paciente: João Carlos Pereira\n"
        "Resumo Clínico: A paciente Maria da Silva foi avaliada e o documento "
        f"{generated_cpf_formatted} foi anexado, repetido como {generated_cpf_formatted} "
        "no rodapé.\n"
    )
    _stub_engine(
        monkeypatch,
        [
            _ner_result("PERSON", text, "João Carlos Pereira"),
            _ner_result("PERSON", text, "Maria da Silva"),
        ],
    )

    result = anonymize_text(text)
    output = result.anonymized_text

    assert "<PESSOA_1>" in output
    assert "<PESSOA_2>" in output
    assert output.count("<CPF_1>") == 2
    assert "João Carlos Pereira" not in output
    assert "Maria da Silva" not in output
    assert generated_cpf_formatted not in output

    mapping = result.pseudonym_map
    assert len(mapping) == 3
    assert mapping["<PESSOA_1>"] == {"value": "João Carlos Pereira", "entity_type": "PESSOA"}
    assert mapping["<PESSOA_2>"]["value"] == "Maria da Silva"
    assert mapping["<CPF_1>"] == {
        "value": generated_cpf_formatted,
        "entity_type": "CPF",
    }
    assert result.anonymization_report["counts_by_type"] == {"PESSOA": 2, "CPF": 2}


def test_repeated_value_same_token(monkeypatch: pytest.MonkeyPatch, generated_cpf: str) -> None:
    """R1: repetição do MESMO valor → MESMO token (e uma única entrada no mapa).

    A repetição do nome no corpo usa CAIXA variante ("Ana Paula Ribeiro" vs.
    rótulo "ANA PAULA RIBEIRO"): a chave canônica por categoria (casefold +
    whitespace colapsado) faz as variantes caírem no MESMO token, e o mapa guarda
    a forma original da PRIMEIRA ocorrência (a do rótulo).
    """
    cpf = f"{generated_cpf[0:3]}.{generated_cpf[3:6]}.{generated_cpf[6:9]}-{generated_cpf[9:11]}"
    text = (
        f"Paciente: ANA PAULA RIBEIRO\n"
        f"Resumo Clínico: A paciente compareceu em duas ocasiões. CPF {cpf} na "
        f"primeira consulta e o mesmo CPF {cpf} na segunda consulta, sempre sob "
        "o nome Ana Paula Ribeiro.\n"
    )
    # NER não reconhece nada: nome e CPF são cobertos só pelo caminho determinístico.
    _stub_engine(monkeypatch, [])

    result = anonymize_text(text)
    output = result.anonymized_text

    assert output.count("<PESSOA_1>") == 2  # variante de caixa do nome = mesmo token
    assert output.count("<CPF_1>") == 2  # CPF repetido = mesmo token
    assert "ANA PAULA RIBEIRO" not in output
    assert "Ana Paula Ribeiro" not in output
    assert generated_cpf not in output

    mapping = result.pseudonym_map
    assert len(mapping) == 2
    assert mapping["<PESSOA_1>"]["entity_type"] == "PESSOA"
    assert mapping["<PESSOA_1>"]["value"] == "ANA PAULA RIBEIRO"  # 1ª ocorrência (rótulo)
    assert mapping["<CPF_1>"]["entity_type"] == "CPF"


# ── R2/R3: merge determinístico e varredura zero-PII ──────────────────────


def test_deterministic_span_guaranteed_without_ner(
    monkeypatch: pytest.MonkeyPatch, generated_cpf_formatted: str
) -> None:
    """R2/R3: nome que o NER não reconhece (nenhum resultado) ainda é tokenizado.

    O span determinístico do rótulo do relatório (nome/nascimento/CPF) é
    garantido pelo merge mesmo quando o analyzer devolve vazio — o corpo
    anonimizado não contém os valores originais.
    """
    text = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        "Paciente: MARIA DA SILVA SOUZA\n"
        "Nascimento: 15/08/1955\n"
        f"CPF: {generated_cpf_formatted}\n"
        "Resumo Clínico: acompanhamento ambulatorial de rotina.\n"
    )
    _stub_engine(monkeypatch, [])

    result = anonymize_text(text)
    output = result.anonymized_text

    assert "<PESSOA_1>" in output
    assert "<DATA_1>" in output
    assert "<CPF_1>" in output
    assert "MARIA DA SILVA SOUZA" not in output
    assert "15/08/1955" not in output
    assert generated_cpf_formatted not in output

    counts = result.anonymization_report["counts_by_type"]
    assert counts == {"PESSOA": 1, "DATA": 1, "CPF": 1, "OCORRENCIA": 1}


def test_zero_pii_sweep(
    monkeypatch: pytest.MonkeyPatch, generated_cpf_formatted: str, generated_cns: str
) -> None:
    """R2/R3: varredura zero-PII — checksum CPF/CNS + valores originais ausentes.

    Após a anonimização de um relatório sintético completo, nenhum run de
    dígitos do output passa no checksum de CPF/CNS e nenhum valor original
    (nome, nascimento, nº de ocorrência, CPF, CNS) permanece no corpo. A data de
    nascimento reaparece no corpo em grafia variante ("2-2-1960" vs.
    "02/02/1960") e cai no MESMO token DATA. O checksum roda DE FATO sobre os
    candidatos — inclusive de um output adversário com CPF/CNS válidos vazados,
    que os validadores DETECTAM — sem depender do assert de lista vazia.
    """
    cpf = generated_cpf_formatted
    text = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        "Paciente: JOSE CARLOS DE OLIVEIRA\n"
        "Data de Nascimento: 02/02/1960\n"
        f"CPF: {cpf}\n"
        f"CNS: {generated_cns}\n"
        "Resumo Clínico: paciente internado com quadro de dor torácica; "
        "encaminhado para avaliação hemodinâmica; retorno em 2-2-1960.\n"
    )
    _stub_engine(monkeypatch, [])

    result = anonymize_text(text)
    output = result.anonymized_text

    # 1. Nenhum checksum válido sobrevive no corpo anonimizado: o validador roda
    #    sobre cada candidato extraído do output (os loops vêm ANTES do assert de
    #    lista vazia — sozinho, ele nunca exercitaria o checksum).
    for candidate in extract_cpf_candidates(output):
        assert valid_cpf(candidate) is False
    for candidate in extract_cns_candidates(output):
        assert valid_cns(candidate) is False
    assert extract_cpf_candidates(output) == ()
    assert extract_cns_candidates(output) == ()

    # 2. Contraprova do método: um output adversário com CPF/CNS válidos vazados
    #    é DETECTADO pelos validadores (a varredura não é tautológica).
    leaked_cpf = extract_cpf_candidates(f"resumo vazando {cpf} no corpo")
    assert leaked_cpf
    assert valid_cpf(leaked_cpf[0]) is True
    leaked_cns = extract_cns_candidates(f"resumo vazando {generated_cns} no corpo")
    assert leaked_cns
    assert valid_cns(leaked_cns[0]) is True

    # 3. Valores originais ausentes (tokens estáveis no lugar).
    assert "JOSE CARLOS DE OLIVEIRA" not in output
    assert "02/02/1960" not in output
    assert "2-2-1960" not in output
    assert cpf not in output
    assert generated_cns not in output
    assert "33345" not in output
    assert output.count("<DATA_1>") == 2  # "2-2-1960" e "02/02/1960" = MESMO token
    assert "<PESSOA_1>" in output
    assert "<OCORRENCIA_1>" in output

    mapping = result.pseudonym_map
    # Mapa 1:1 — pré-requisito do roundtrip do slice 005.
    values = [entry["value"] for entry in mapping.values()]
    assert len(values) == len(set(values))
    assert mapping["<CPF_1>"]["value"] == cpf
    assert mapping["<CNS_1>"]["value"] == generated_cns
    assert mapping["<DATA_1>"]["value"] == "02/02/1960"  # 1ª ocorrência (rótulo) decide a grafia


# ── R2: política de sobreposição do merge (casos da matriz) ───────────────


def test_merge_same_category_overlap_keeps_outer() -> None:
    """R2: sobreposição de MESMA categoria — o primeiro da ordem vence."""
    candidates = [
        SpanCandidate(0, 12, "PESSOA", deterministic=False),
        SpanCandidate(4, 9, "PESSOA", deterministic=False),
    ]

    assert merge_span_candidates(candidates) == [candidates[0]]


def test_merge_different_categories_resolves_by_order() -> None:
    """R2: categorias DIFERENTES na mesma origem — largura decide (end desc)."""
    candidates = [
        SpanCandidate(0, 6, "DATA", deterministic=False),
        SpanCandidate(0, 12, "PESSOA", deterministic=False),
    ]

    assert merge_span_candidates(candidates) == [SpanCandidate(0, 12, "PESSOA", False)]


def test_merge_partial_overlap_keeps_first() -> None:
    """R2: contenção PARCIAL — mais à esquerda vence, o restante é descartado."""
    candidates = [
        SpanCandidate(0, 10, "PESSOA", deterministic=False),
        SpanCandidate(7, 15, "DATA", deterministic=True),
    ]

    assert merge_span_candidates(candidates) == [candidates[0]]


def test_merge_contained_overlap_dropped() -> None:
    """R2: contenção TOTAL — o span contido some, o contêiner permanece."""
    candidates = [
        SpanCandidate(2, 20, "PESSOA", deterministic=True),
        SpanCandidate(5, 10, "CPF", deterministic=True),
    ]

    assert merge_span_candidates(candidates) == [candidates[0]]


def test_merge_tie_deterministic_beats_ner() -> None:
    """R2: EMPATE de offsets — determinístico vence o NER no mesmo span."""
    candidates = [
        SpanCandidate(3, 11, "PESSOA", deterministic=False),
        SpanCandidate(3, 11, "PESSOA", deterministic=True),
    ]

    assert merge_span_candidates(candidates) == [candidates[1]]


def test_merge_keeps_disjoint_spans_sorted() -> None:
    """R2: spans disjuntos permanecem todos, em ordem de offset."""
    candidates = [
        SpanCandidate(10, 15, "CPF", deterministic=True),
        SpanCandidate(0, 5, "PESSOA", deterministic=True),
        SpanCandidate(6, 9, "DATA", deterministic=False),
    ]

    assert merge_span_candidates(candidates) == [
        SpanCandidate(0, 5, "PESSOA", True),
        SpanCandidate(6, 9, "DATA", False),
        SpanCandidate(10, 15, "CPF", True),
    ]


# ── R4/R5: wrapper transacional + campos/evento no caso ───────────────────


@pytest.mark.django_db
def test_case_fields_and_event(
    monkeypatch: pytest.MonkeyPatch, generated_cpf_formatted: str
) -> None:
    """R4/R5: wrapper persiste artefatos, linkage e grava o evento canônico.

    ``anonymized_text``/``pseudonym_map``/``anonymization_report`` +
    ``patient_name``/``patient_birth_date`` (+ ``agency_record_number`` quando
    ainda vazio) no ``Case`` e ``CASE_ANONYMIZATION_COMPLETED`` na trilha, tudo
    numa transação. Numa 2ª execução sem os campos, o linkage velho é
    SOBRESCRITO por vazio (só ``agency_record_number`` é preservado-se-vazio).
    Nenhuma transição de estado acontece aqui (é o slice 004).
    """
    user = User.objects.create_user(username="anon-teste", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    assert case.status == CaseStatus.ANONYMIZING

    text = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        "Paciente: MARIA DA SILVA SOUZA\n"
        "Data de Nascimento: 15/08/1955\n"
        f"CPF: {generated_cpf_formatted}\n"
        "Resumo Clínico: paciente em acompanhamento para cateterismo.\n"
    )
    case.extracted_text = text
    case.save(update_fields=["extracted_text"])
    _stub_engine(monkeypatch, [])

    result = anonymize_case_text(case)
    case.refresh_from_db()

    # Artefatos persistidos.
    assert case.anonymized_text == result.anonymized_text
    assert "<PESSOA_1>" in case.anonymized_text
    assert "MARIA DA SILVA SOUZA" not in case.anonymized_text
    assert case.pseudonym_map == result.pseudonym_map
    assert case.anonymization_report == result.anonymization_report
    assert case.anonymization_report["counts_by_type"] == {
        "PESSOA": 1,
        "DATA": 1,
        "CPF": 1,
        "OCORRENCIA": 1,
    }
    # Linkage persistido (consumido pelos prior-case 06/presenter 07).
    assert case.patient_name == "MARIA DA SILVA SOUZA"
    assert case.patient_birth_date == date(1955, 8, 15)
    assert case.agency_record_number == "33345"

    # Evento canônico com payload enxuto (contagens + versões).
    event = case.events.filter(event_type=CaseEventType.CASE_ANONYMIZATION_COMPLETED.value).get()
    assert event.actor_type == ActorType.SYSTEM
    assert event.actor_role == SYSTEM_ROLE
    assert event.payload["counts_by_type"] == {"PESSOA": 1, "DATA": 1, "CPF": 1, "OCORRENCIA": 1}
    assert "presidio_analyzer_version" in event.payload
    assert "presidio_anonymizer_version" in event.payload
    # Sem transição de estado neste slice.
    assert case.status == CaseStatus.ANONYMIZING

    # Reprocessamento (P2/review): 2ª execução com texto SEM nome/nascimento → o
    # linkage antigo NÃO sobrevive — os campos viram vazios; o único preservado-
    # se-vazio é ``agency_record_number`` (contrato R4).
    case.extracted_text = "Resumo Clínico: acompanhamento ambulatorial de rotina.\n"
    case.save(update_fields=["extracted_text"])
    anonymize_case_text(case)
    case.refresh_from_db()

    assert case.patient_name == ""
    assert case.patient_birth_date is None
    assert case.agency_record_number == "33345"  # preservado-se-vazio
    assert case.status == CaseStatus.ANONYMIZING


@pytest.mark.django_db
def test_wrapper_rolls_back_artifacts_on_error(
    monkeypatch: pytest.MonkeyPatch, generated_cpf: str
) -> None:
    """R4: exceção no pipeline → nada é persistido (transação única)."""
    user = User.objects.create_user(username="anon-rollback", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.extracted_text = f"Paciente: ANA PAULA RIBEIRO\nCPF: {generated_cpf}\n"
    case.save(update_fields=["extracted_text"])

    fake = AnonymizationEngine(analyzer=_ExplodingAnalyzer(), anonymizer=None)
    monkeypatch.setattr("apps.anonymization.services.get_anonymization_engine", lambda: fake)

    with pytest.raises(RuntimeError):
        anonymize_case_text(case)
    case.refresh_from_db()

    assert case.anonymized_text == ""
    assert case.pseudonym_map == {}
    assert case.anonymization_report == {}
    assert case.patient_name == ""
    assert not case.events.filter(
        event_type=CaseEventType.CASE_ANONYMIZATION_COMPLETED.value
    ).exists()


# ── R6: texto vazio defensivo ─────────────────────────────────────────────


def test_empty_text_defensive() -> None:
    """R6: texto vazio → zero entidades e ``anonymized_text=""`` (sem erro).

    O caso que chegou extraído vazio deveria ter sido retido pelo gate (task do
    slice 004); aqui o núcleo é apenas defensivo — não levanta exceção.
    """
    result = anonymize_text("")

    assert result.anonymized_text == ""
    assert result.pseudonym_map == {}
    assert result.anonymization_report["counts_by_type"] == {}
    assert result.extraction.record_number is None
