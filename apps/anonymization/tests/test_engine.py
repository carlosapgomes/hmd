"""Testes do engine Presidio (slice 002, R4/R6).

Carregam o modelo spaCy ``pt_core_news_lg`` (lentos, ok na suíte) através do
singleton — a primeira fixture da sessão paga o custo do carregamento e as
demais chamadas reusam a instância em cache. Os testes de persona/CPF usam
texto sintético e valores gerados nas fixtures (sem PII real).
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from presidio_analyzer import Pattern, PatternRecognizer
from presidio_anonymizer import AnonymizerEngine

from apps.anonymization.engine import (
    DEFAULT_SCORE_THRESHOLD,
    AnonymizationEngine,
    get_anonymization_engine,
    score_threshold,
)


@pytest.fixture(scope="session")
def engine() -> AnonymizationEngine:
    """Engine singleton carregado uma única vez por processo de teste."""
    return get_anonymization_engine()


# ── R4: singleton e limiar ────────────────────────────────────────────────


def test_singleton_same_instance() -> None:
    """R4: chamadas repetidas retornam a MESMA instância (lru_cache maxsize=1)."""
    assert get_anonymization_engine() is get_anonymization_engine()


def test_engine_exposes_anonymizer(engine: AnonymizationEngine) -> None:
    """R4: o singleton expõe o analyzer E o anonymizer do Presidio."""
    assert engine.analyzer is get_anonymization_engine().analyzer
    assert isinstance(engine.anonymizer, AnonymizerEngine)


def test_score_threshold_default_and_setting() -> None:
    """R4: limiar default 0.45 e leitura do setting ANONYMIZATION_SCORE_THRESHOLD."""
    assert DEFAULT_SCORE_THRESHOLD == 0.45
    with override_settings(ANONYMIZATION_SCORE_THRESHOLD=0.72):
        assert score_threshold() == 0.72


# ── R6: detecção no texto pt (modelo carregado) ───────────────────────────


def test_detects_person_pt(engine: AnonymizationEngine) -> None:
    """R6: NER detecta PERSON (nome) em texto sintético em português."""
    text = "O paciente João Carlos Pereira, 45 anos, deu entrada na emergência."
    results = engine.analyzer.analyze(
        text=text,
        language="pt",
        entities=["PERSON"],
        score_threshold=score_threshold(),
    )

    person_matches = [text[result.start : result.end] for result in results]
    assert person_matches, "Nenhum PERSON detectado pelo modelo pt"
    assert any("João" in match or "Pereira" in match for match in person_matches)


def test_detects_generated_cpf(engine: AnonymizationEngine, generated_cpf: str) -> None:
    """R6: BR_CPF de CPF válido gerado é detectado no texto."""
    cpf = f"{generated_cpf[0:3]}.{generated_cpf[3:6]}.{generated_cpf[6:9]}-{generated_cpf[9:11]}"
    text = f"Foi anexado o número {cpf} ao processo do paciente."
    results = engine.analyzer.analyze(
        text=text,
        language="pt",
        entities=["BR_CPF"],
        score_threshold=score_threshold(),
    )

    cpf_results = [
        text[result.start : result.end] for result in results if result.entity_type == "BR_CPF"
    ]
    assert cpf_results and cpf_results[0] == cpf


def test_score_threshold_respected(engine: AnonymizationEngine, generated_cpf: str) -> None:
    """R6: limiar de score é respeitado na análise (CPF de score fixo 0.85).

    Acima do score do recognizer o resultado some; no limiar do engine
    (default 0.45) o BR_CPF passa.
    """
    cpf = f"{generated_cpf[0:3]}.{generated_cpf[3:6]}.{generated_cpf[6:9]}-{generated_cpf[9:11]}"
    text = f"Foi anexado o número {cpf} ao processo."

    strict = engine.analyzer.analyze(
        text=text,
        language="pt",
        entities=["BR_CPF"],
        score_threshold=0.99,
    )
    assert strict == []

    relaxed = engine.analyzer.analyze(
        text=text,
        language="pt",
        entities=["BR_CPF"],
        score_threshold=score_threshold(),
    )
    assert [result.entity_type for result in relaxed] == ["BR_CPF"]


# ── P1 (review): default do engine segue o setting ────────────────────────


def _analyze_with_engine_default(
    engine: AnonymizationEngine,
    text: str,
    entities: list[str],
    ad_hoc_recognizers: list[PatternRecognizer],
) -> list[str]:
    """Chama ``analyze`` SEM ``score_threshold`` (usa o default do engine)."""
    results = engine.analyzer.analyze(
        text=text,
        language="pt",
        entities=entities,
        ad_hoc_recognizers=ad_hoc_recognizers,
    )
    return sorted(result.entity_type for result in results)


def test_engine_default_score_threshold_comes_from_setting(
    engine: AnonymizationEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1: analyze sem ``score_threshold`` aplica o default vindo do setting.

    O default do Presidio é capturado na construção do AnalyzerEngine
    (``default_score_threshold=score_threshold()``); sem isso o Presidio
    assume 0 e o setting ANONYMIZATION_SCORE_THRESHOLD era ignorado. Como o
    default é fixado na construção, o teste reconstrói o singleton sob
    ``override_settings`` reusando o modelo já carregado do engine da sessão
    (monkeypatch em ``_build_nlp_engine`` — sem novo load do modelo).
    """
    text = "sinal_baixo sinal_medio sinal_alto"
    entities = ["SCORE_BAIXO", "SCORE_MEDIO", "SCORE_ALTO"]
    ad_hoc = [
        PatternRecognizer(
            supported_entity=entity,
            supported_language="pt",
            patterns=[Pattern(name=f"padrao_{token}", regex=rf"\b{token}\b", score=score)],
        )
        for entity, token, score in [
            ("SCORE_BAIXO", "sinal_baixo", 0.3),
            ("SCORE_MEDIO", "sinal_medio", 0.65),
            ("SCORE_ALTO", "sinal_alto", 0.85),
        ]
    ]
    loaded_nlp = engine.analyzer.nlp_engine
    monkeypatch.setattr("apps.anonymization.engine._build_nlp_engine", lambda: loaded_nlp)

    def detect(threshold: float) -> list[str]:
        with override_settings(ANONYMIZATION_SCORE_THRESHOLD=threshold):
            get_anonymization_engine.cache_clear()
            rebuilt = get_anonymization_engine()
            return _analyze_with_engine_default(rebuilt, text, entities, ad_hoc)

    try:
        # Default do setting (0.45): acima do limiar detectado, abaixo (0.3) não.
        assert detect(DEFAULT_SCORE_THRESHOLD) == ["SCORE_ALTO", "SCORE_MEDIO"]
        # Setting alto (0.8): a detecção marginal (0.65) some SEM argumento.
        assert detect(0.8) == ["SCORE_ALTO"]
    finally:
        get_anonymization_engine.cache_clear()
        # Reconstrói o singleton no default para o restante da suíte.
        get_anonymization_engine()
