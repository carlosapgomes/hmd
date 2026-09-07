"""Engine Presidio singleton pt-BR (slice 002, change presidio-anonymization;
design D4, "Engine singleton" — requisito R4).

Carrega o NlpEngine spaCy a partir de ``config/presidio-nlp-pt.yaml`` (mapping
PER/LOC/ORG/DATE→Presidio + low-confidence da pesquisa §5), com o modelo
``ANONYMIZATION_SPACY_MODEL`` (default ``pt_core_news_lg``), o
``RecognizerRegistry`` com os predefined para ``pt`` + os recognizers BR
(CPF/CNS/CRM), o ``AnalyzerEngine(supported_languages=["pt"])`` e o
``AnonymizerEngine``.

``get_anonymization_engine()`` é singleton por processo
(``lru_cache(maxsize=1)``) e o carregamento do modelo (centenas de MB de RSS)
acontece uma única vez — apenas em quem importa este módulo (worker/testes;
nunca no processo web; design D1/D4). O limiar de score do analyzer vem de
``ANONYMIZATION_SCORE_THRESHOLD`` (default 0.45; setting env-driven em
``config/settings/base.py``), capturado como ``default_score_threshold`` na
construção do ``AnalyzerEngine``: sem isso o Presidio assume 0 e chamadas de
``analyze`` sem ``score_threshold`` ignorariam o setting (P1 do review).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine

from apps.anonymization.recognizers import CnsRecognizer, CpfRecognizer, CrmRecognizer

# ``apps/anonymization/engine.py`` → raiz do projeto (parents[2])/config/….
_NLP_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "presidio-nlp-pt.yaml"

DEFAULT_SPACY_MODEL = "pt_core_news_lg"
DEFAULT_SCORE_THRESHOLD = 0.45


@dataclass(frozen=True)
class AnonymizationEngine:
    """Par analyzer/anonymizer do Presidio, criados juntos no singleton (R4).

    O analyzer expõe ``analyze(...)`` (detecção) e o anonymizer
    ``anonymize(...)`` (substituição) — consumidos pelo núcleo de anonimização
    do slice 003.
    """

    analyzer: Any
    anonymizer: Any


def _setting(name: str, default: Any) -> Any:
    """Valor do setting do Django quando configurado; senão, variável de ambiente.

    Permite importar este módulo fora de um contexto Django (a leitura do
    valor só ocorre na chamada) mantendo o contrato: com settings carregadas,
    o valor vem do setting env-driven de ``config/settings/base.py``.
    """
    try:
        return getattr(settings, name)
    except (ImproperlyConfigured, AttributeError):
        return os.environ.get(name, default)


def spacy_model_name() -> str:
    """Nome do modelo spaCy pt (``ANONYMIZATION_SPACY_MODEL``; default ``pt_core_news_lg``)."""
    return str(_setting("ANONYMIZATION_SPACY_MODEL", DEFAULT_SPACY_MODEL))


def score_threshold() -> float:
    """Limiar de score do analyzer (``ANONYMIZATION_SCORE_THRESHOLD``; default 0.45)."""
    return float(_setting("ANONYMIZATION_SCORE_THRESHOLD", DEFAULT_SCORE_THRESHOLD))


def _nlp_configuration() -> dict[str, Any]:
    """Configuração do NlpEngine do YAML com o modelo trocável por env.

    O YAML (config/presidio-nlp-pt.yaml) é a fonte do mapeamento NER e da
    política de low-confidence; apenas o nome do modelo é sobrescrito pelo
    setting ``ANONYMIZATION_SPACY_MODEL``.
    """
    with _NLP_CONFIG_PATH.open(encoding="utf-8") as handle:
        configuration = yaml.safe_load(handle)
    if not isinstance(configuration, dict):
        raise RuntimeError(f"Configuração NLP inválida (esperado dict): {_NLP_CONFIG_PATH}")
    model_name = spacy_model_name()
    for model in configuration.get("models", []):
        if model.get("lang_code") == "pt":
            model["model_name"] = model_name
    return configuration


def _build_nlp_engine() -> Any:
    """Cria o NlpEngine spaCy (carrega o modelo nesta chamada — custo dominante)."""
    provider = NlpEngineProvider(nlp_configuration=_nlp_configuration())
    return provider.create_engine()


def _build_registry(nlp_engine: Any) -> RecognizerRegistry:
    """Registry com os recognizers predefined de ``pt`` + os recognizers BR.

    O registry nasce com ``supported_languages=["pt"]`` — o AnalyzerEngine
    exige consistência entre as línguas do registry e as do analyzer.
    """
    registry = RecognizerRegistry(supported_languages=["pt"])
    registry.load_predefined_recognizers(languages=["pt"], nlp_engine=nlp_engine)
    registry.add_recognizer(CpfRecognizer())
    registry.add_recognizer(CnsRecognizer())
    registry.add_recognizer(CrmRecognizer())
    return registry


@lru_cache(maxsize=1)
def get_anonymization_engine() -> AnonymizationEngine:
    """Engine singleton: NlpEngine → registry (predefined + BR) → analyzer + anonymizer.

    ``lru_cache(maxsize=1)`` garante uma única instância por processo — o
    carregamento do modelo acontece na primeira chamada e é reusado nas
    seguintes (``get_anonymization_engine() is get_anonymization_engine()``).
    """
    nlp_engine = _build_nlp_engine()
    analyzer = AnalyzerEngine(
        nlp_engine=nlp_engine,
        registry=_build_registry(nlp_engine),
        supported_languages=["pt"],
        # P1 do review: o default do engine segue o setting, senão o Presidio
        # assume 0 e analyze(..., score_threshold=None) ignora o limiar.
        default_score_threshold=score_threshold(),
    )
    # AnonymizerEngine é construído sem anotações de tipo no pacote (py.typed
    # parcial) — o contrato é validado pelos testes do slice, não por mypy.
    anonymizer = AnonymizerEngine()  # type: ignore[no-untyped-call]
    return AnonymizationEngine(analyzer=analyzer, anonymizer=anonymizer)
