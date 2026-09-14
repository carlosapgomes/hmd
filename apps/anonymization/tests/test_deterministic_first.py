"""Testes do modo determinístico-first da anonimização (change
anonymization-deterministic-first, slice 001; R1–R3, design D1/D3/D4/D6).

Cobre: (a) o gate do setting ``ANONYMIZATION_USE_NER`` (default DESLIGADO) — com
o NER off o engine Presidio/spaCy não é construído nem consultado, o mapa é só
determinístico e o relatório marca ``ner_enabled`` sem descrever engine que não
rodou (D6); (b) a reversibilidade por env (override True) — os candidatos do NER
voltam a ser somados (comportamento anterior preservado); (c) a varredura dos
valores SEMEADOS (D3, ampliação aprovada): valores CONHECIDOS do caso citados no
texto sem rótulo SESAB viram candidatos determinísticos — motivo de negatura e
anexos — com o MESMO token do caso e sem vazar valores não usados; (d) o pino E2E
do incidente 3ca56d86: texto SESAB canônico com o vocabulário clínico que o NER
classificava como PESSOA/LOCAL e um artefato de LLM representativo (com
"profissional"/"Registro"/"Macro") passam no guard ``_assert_tokens_only`` com o
mapa limpo.

O engine é SEMPRE um analyzer fake (patch no módulo de serviços): o modelo spaCy
real não carrega na suíte (padrão de ``test_services.py``).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.test import override_settings

from apps.accounts.models import User
from apps.anonymization import services
from apps.anonymization.engine import AnonymizationEngine
from apps.anonymization.services import (
    anonymize_attachment_text,
    anonymize_case_text,
    anonymize_text,
)
from apps.cases.events import CaseEventType
from apps.cases.models import Case
from apps.pipeline.llm1_service import _assert_tokens_only

_NER_OFF = override_settings(ANONYMIZATION_USE_NER=False)
_NER_ON = override_settings(ANONYMIZATION_USE_NER=True)

# Relatório SESAB canônico do incidente (rótulos de identificação do paciente +
# corpo com TODO o vocabulário clínico genérico listado no Contexto do slice —
# sem dado de paciente real).
_INCIDENT_TEXT = (
    "RELATÓRIO DE OCORRÊNCIAS\n"
    "Central Estadual de Regulação\n"
    "Secretaria da Saúde do Estado da Bahia\n"
    "Código: 33345\n"
    "Nome do Paciente: MARIA DA SILVA SOUZA\n"
    "Data de Nascimento: 15/08/1955\n"
    "CNS: 547762666886141\n"
    "Motivo da Solicitação: cateterismo cardíaco diagnóstico\n"
    "Resumo Clínico: paciente Afebril, Diurese presente, dor em DORSO, DOENÇA ARTERIAL "
    "PERIFÉRICA conhecida, em uso de Elicris; exame com Murmúrio vesicular presente, MID "
    "com pulsos diminuídos, PAS:100\n"
    "PAD:80, FC:77\n"
    "FR:18, FC:74\n"
    "FR:18\n"
    "PAS:120; Macro de ARTERIOGRAFIA de membro inferior; Prof. plantonista, Reg. CREMEB "
    "12345; Informado MG; Dias Unid: 3; D0 em tela; Mot. da Solicitação conforme Nordeste\n"
    "Motivo da Solicitação do documento. Solicit. exame complementar. Paciente "
    "MARIA DA SILVA SOUZA retorna para reavaliação.\n"
)

# Fragmentos que o NER do incidente classificou como PESSOA/LOCAL (fantasmais).
_INCIDENT_PHANTOM_VALUES: tuple[tuple[str, str], ...] = (
    ("PERSON", "Afebril"),
    ("PERSON", "DORSO"),
    ("PERSON", "Prof"),
    ("PERSON", "Dias Unid"),
    ("LOCATION", "Macro"),
    ("LOCATION", "Murmúrio"),
    ("LOCATION", "CREMEB"),
    ("LOCATION", "Informado"),
)

# Artefato de LLM representativo: vocabulário natural que "reconhece" os termos
# fantasmais como substring ("profissional", "Registro") e um valor exato
# ("Macro") — o guard só passa porque o mapa é limpo.
_LLM_ARTIFACT: dict[str, object] = {
    "resumo_clinico": ("Paciente <PESSOA_1> estável; achado informado ao profissional de plantão."),
    "achados": [
        "DORSO sem alterações",
        "Murmúrio vesicular presente",
        "Macro de arteriografia anexado",
        "Registro em evolução",
    ],
    "conduta": "Solicitar exame complementar.",
}


class _RecordingAnalyzer:
    """Analyzer fake: devolve results pré-configurados e conta as consultas."""

    def __init__(self, results: list[SimpleNamespace]) -> None:
        self._results = list(results)
        self.calls = 0

    def analyze(self, **kwargs: object) -> list[SimpleNamespace]:
        del kwargs
        self.calls += 1
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


def _stub_engine(monkeypatch: pytest.MonkeyPatch, analyzer: _RecordingAnalyzer) -> None:
    """Substitui o singleton do engine por um analyzer fake (sem modelo)."""
    fake = AnonymizationEngine(analyzer=analyzer, anonymizer=None)
    monkeypatch.setattr(services, "get_anonymization_engine", lambda: fake)


def _exploding_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch que LEVANTA se o engine for construído (prova do gate com NER off)."""

    def _boom() -> AnonymizationEngine:
        raise AssertionError("engine Presidio construído/consultado com NER desligado")

    monkeypatch.setattr(services, "get_anonymization_engine", _boom)


def _incident_phantoms() -> list[SimpleNamespace]:
    """Results fantasmais do incidente (mesmos valores/entidades do mapa real)."""
    return [
        _ner_result(entity_type, _INCIDENT_TEXT, value)
        for entity_type, value in _INCIDENT_PHANTOM_VALUES
    ]


# ── R1/D1: gate do setting (default desligado) ────────────────────────────


def test_ner_off_does_not_build_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """R1/D1: com o NER off o engine não é construído nem consultado.

    O patch levanta se ``get_anonymization_engine`` for chamado: o mapa/report
    só podem sair da extração determinística (rótulos do relatório).
    """
    _exploding_engine(monkeypatch)

    with _NER_OFF:
        result = anonymize_text(_INCIDENT_TEXT)

    assert set(result.pseudonym_map) == {
        "<PESSOA_1>",
        "<DATA_1>",
        "<CNS_1>",
        "<OCORRENCIA_1>",
    }
    assert result.anonymization_report["counts_by_type"] == {
        "PESSOA": 2,  # nome no rótulo + repetido no corpo = MESMO token
        "DATA": 1,
        "CNS": 1,
        "OCORRENCIA": 1,
    }


def test_ner_off_report_is_truthful(monkeypatch: pytest.MonkeyPatch) -> None:
    """R1/D6: com NER off o relatório marca ``ner_enabled`` e omite engine/versões.

    O artefato de auditoria nunca descreve um engine que não rodou.
    """
    _exploding_engine(monkeypatch)

    with _NER_OFF:
        result = anonymize_text(_INCIDENT_TEXT)

    report = result.anonymization_report
    assert report["ner_enabled"] is False
    assert set(report) == {"counts_by_type", "ner_enabled"}


def test_ner_off_keeps_clinical_vocabulary_in_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """R1/cenário spec: nenhum termo clínico fantasma recebe token com NER off."""
    _exploding_engine(monkeypatch)

    with _NER_OFF:
        result = anonymize_text(_INCIDENT_TEXT)

    output = result.anonymized_text
    for _, value in _INCIDENT_PHANTOM_VALUES:
        assert value in output
    values = [entry["value"] for entry in result.pseudonym_map.values()]
    assert len(values) == 4  # só o linkage determinístico do paciente


# ── R1/D1: reversibilidade por env (caminho NER preservado) ───────────────


def test_ner_on_sums_ner_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """R1/cenário spec: NER=true soma os candidatos do analyzer de volta.

    O caminho anterior é preservado: o analyzer é consultado, o span NER vira
    token e o relatório volta a trazer o engine/versões.
    """
    text = (
        "RELATÓRIO DE OCORRÊNCIAS\n"
        "Código: 33345\n"
        "Nome do Paciente: MARIA DA SILVA SOUZA\n"
        "Resumo Clínico: acompanhamento com o Dr. Carlos Alberto Pereira.\n"
    )
    analyzer = _RecordingAnalyzer([_ner_result("PERSON", text, "Carlos Alberto Pereira")])
    _stub_engine(monkeypatch, analyzer)

    with _NER_ON:
        result = anonymize_text(text)

    assert analyzer.calls == 1
    assert "Carlos Alberto Pereira" not in result.anonymized_text
    assert "<PESSOA_1>" in result.anonymized_text  # nome do rótulo (determinístico)
    assert "<PESSOA_2>" in result.anonymized_text  # nome do NER
    assert result.anonymization_report["ner_enabled"] is True
    assert "model" in result.anonymization_report
    assert "presidio_analyzer_version" in result.anonymization_report


# ── R2/D3: varredura dos valores semeados (valores conhecidos do caso) ────


def test_seed_without_label_reuses_case_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2/D3: valor do seed citado SEM rótulo SESAB e com NER off → token do caso.

    O nome do paciente num texto livre (motivo de negatura) não tem rótulo; a
    varredura dos valores CONHECIDOS do caso o localiza e o operador reutiliza o
    ``<PESSOA_2>`` do caso (nunca um token novo).
    """
    _exploding_engine(monkeypatch)
    seed = {"<PESSOA_2>": {"value": "Maria da Silva", "entity_type": "PESSOA"}}
    reason = "Maria da Silva apresenta INR elevado e plaquetas baixas — não liberar."

    with _NER_OFF:
        result = anonymize_text(reason, seed_map=seed)

    assert result.anonymized_text == (
        "<PESSOA_2> apresenta INR elevado e plaquetas baixas — não liberar."
    )
    assert result.pseudonym_map == {
        "<PESSOA_2>": {"value": "Maria da Silva", "entity_type": "PESSOA"}
    }


def test_seed_value_absent_from_text_not_in_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2/D3: valores do seed ausentes do texto NÃO entram no mapa resultado."""
    _exploding_engine(monkeypatch)
    seed = {
        "<PESSOA_1>": {"value": "JOSE CARLOS DE OLIVEIRA", "entity_type": "PESSOA"},
        "<PESSOA_2>": {"value": "Maria da Silva", "entity_type": "PESSOA"},
        "<CPF_1>": {"value": "955.075.869-93", "entity_type": "CPF"},
    }
    text = "Maria da Silva não pode receber contraste."

    with _NER_OFF:
        result = anonymize_text(text, seed_map=seed)

    assert "JOSE CARLOS DE OLIVEIRA" not in result.anonymized_text
    assert result.anonymized_text == "<PESSOA_2> não pode receber contraste."
    assert set(result.pseudonym_map) == {"<PESSOA_2>"}


def test_seed_scan_is_boundary_aware(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2/D3: a varredura nunca casa o valor no MEIO de uma palavra.

    Um valor curto do seed ("Reg") não tokeniza "Registro" (substring), só a
    ocorrência com borda de palavra.
    """
    _exploding_engine(monkeypatch)
    seed = {"<PESSOA_1>": {"value": "Reg", "entity_type": "PESSOA"}}
    text = "Registro do profissional; assinado por Reg. no plantão."

    with _NER_OFF:
        result = anonymize_text(text, seed_map=seed)

    assert (
        result.anonymized_text == "Registro do profissional; assinado por <PESSOA_1>. no plantão."
    )
    assert result.pseudonym_map["<PESSOA_1>"]["value"] == "Reg"


@pytest.mark.django_db
def test_attachment_seed_without_label_reuses_case_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2/D3: anexo citando o paciente sem rótulo e com NER off → token do caso."""
    _exploding_engine(monkeypatch)
    user = User.objects.create_user(username="dono-seed-anexo", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.pseudonym_map = {"<PESSOA_2>": {"value": "Maria da Silva", "entity_type": "PESSOA"}}
    case.save(update_fields=["pseudonym_map"])

    with _NER_OFF:
        result = anonymize_attachment_text(case, "Anexo: Maria da Silva em uso de AAS.")

    assert result.anonymized_text == "Anexo: <PESSOA_2> em uso de AAS."
    assert case.pseudonym_map == {
        "<PESSOA_2>": {"value": "Maria da Silva", "entity_type": "PESSOA"}
    }


# ── R3/D4: pino E2E do incidente (guard com mapa limpo) ───────────────────


@pytest.mark.django_db
def test_incident_end_to_end_guard_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """R3/D4: incidente 3ca56d86 pinado — mapa determinístico e guard passa.

    O NER fake devolve os fragmentos fantasmais (o que o engine real produzia);
    com o setting default DESLIGADO o engine nunca é consultado, o mapa sai só
    determinístico e o artefato representativo (com "profissional"/"Registro"/
    "Macro") passa em ``_assert_tokens_only``. Sem o change o mesmo teste é
    RED: o engine fake é consultado e os fantasmais entram no mapa (o guard
    também levantaria ``llm1_token_leak`` pelo valor exato "Macro" do artefato).
    """
    analyzer = _RecordingAnalyzer(_incident_phantoms())
    _stub_engine(monkeypatch, analyzer)

    with _NER_OFF:
        result = anonymize_text(_INCIDENT_TEXT)

    assert analyzer.calls == 0
    assert set(result.pseudonym_map) == {
        "<PESSOA_1>",
        "<DATA_1>",
        "<CNS_1>",
        "<OCORRENCIA_1>",
    }

    user = User.objects.create_user(username="dono-pino-incidente", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.pseudonym_map = result.pseudonym_map
    case.save(update_fields=["pseudonym_map"])

    serialized = json.dumps(_LLM_ARTIFACT, ensure_ascii=False)
    assert "profissional" in serialized
    assert "Registro" in serialized
    assert "Macro" in serialized
    _assert_tokens_only(case, dict(_LLM_ARTIFACT))

    output = result.anonymized_text
    assert "MARIA DA SILVA SOUZA" not in output
    assert "Macro" in output  # termo clínico segue em claro (não é PII do paciente)


# ── R1/D6: payload do evento do caso omite engine ausente ─────────────────


@pytest.mark.django_db
def test_case_event_omits_engine_metadata_when_ner_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1/D6: com NER off o evento do caso não descreve engine que não rodou."""
    _exploding_engine(monkeypatch)
    user = User.objects.create_user(username="dono-evento-ner-off", password="senha-teste")
    case = Case.objects.create(created_by=user)
    case.start_pdf_extraction(user=None, role="system")
    case.complete_pdf_extraction(user=None, role="system")
    case.extracted_text = _INCIDENT_TEXT
    case.save(update_fields=["extracted_text"])

    with _NER_OFF:
        anonymize_case_text(case)

    event = case.events.filter(event_type=CaseEventType.CASE_ANONYMIZATION_COMPLETED.value).get()
    assert event.payload["ner_enabled"] is False
    assert "model" not in event.payload
    assert "presidio_analyzer_version" not in event.payload


# ── P1 da review (padrão test_prod_default_disabled do intake): o default ──
# ── FALSE é o posture do incidente — um flip silencioso p/ True deixa    ──
# ── a suíte verde e reintroduz a configuração que derruba todo caso.    ──
def test_anonymization_use_ner_default_false_in_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem env definida, prod mantém o NER DESLIGADO (fase 2)."""
    import importlib
    import sys

    monkeypatch.setenv("DJANGO_SECRET_KEY", "prod-test-secret-key-not-real")
    monkeypatch.setenv("DATABASE_URL", "postgres://hmd:h@localhost:5432/hmd_test")
    monkeypatch.delenv("ANONYMIZATION_USE_NER", raising=False)
    sys.modules.pop("config.settings.base", None)
    sys.modules.pop("config.settings.prod", None)
    prod = importlib.import_module("config.settings.prod")

    assert prod.ANONYMIZATION_USE_NER is False


def test_anonymization_use_ner_enabled_by_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env explícita religa o NER (mesmo parse dos outros flags)."""
    import importlib
    import sys

    monkeypatch.setenv("DJANGO_SECRET_KEY", "prod-test-secret-key-not-real")
    monkeypatch.setenv("DATABASE_URL", "postgres://hmd:h@localhost:5432/hmd_test")
    monkeypatch.setenv("ANONYMIZATION_USE_NER", "true")
    sys.modules.pop("config.settings.base", None)
    sys.modules.pop("config.settings.prod", None)
    prod = importlib.import_module("config.settings.prod")

    assert prod.ANONYMIZATION_USE_NER is True
