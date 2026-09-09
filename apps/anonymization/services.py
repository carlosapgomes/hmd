"""Serviço de anonimização com pseudônimos estáveis (slice 003, change
presidio-anonymization; design D6, R2–R5).

Pipeline do núcleo puro ``anonymize_text(text) -> AnonymizationCoreResult``
(sem DB — consumido pelo benchmark do slice 005): (1) pré-extração
determinística (``deterministic.py``), (2) análise do Presidio, (3) merge com a
política determinística completa — TODAS as ocorrências de cada valor
determinístico localizadas no texto com offsets válidos (nome→PESSOA,
nascimento→DATA, nº de ocorrência→OCORRENCIA, CPF→CPF, CNS→CNS, mesmo sem
NER), ordenação por ``(start asc, end desc, determinístico > NER)`` e
sobreposições resolvidas mantendo o primeiro da ordem —, (4) substituição
própria (fallback D5) sobre os spans mesclados via ``PseudonymOperator`` e
(5) artefatos: texto anonimizado + mapa + relatório.

O wrapper ``anonymize_case_text(case)`` persiste os artefatos e o linkage
(``patient_name``/``patient_birth_date``/``agency_record_number``) no ``Case``
e grava o evento ``CASE_ANONYMIZATION_COMPLETED`` numa transação única — sem
transição de estado (a task do slice 004 envolve wrapper + FSM). O núcleo é
defensivo com texto vazio (o porteiro que falha fechado é a task do 004).
"""

from __future__ import annotations

import importlib.metadata
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from django.db import transaction

from apps.anonymization.deterministic import (
    _DIGIT_RUN_PATTERN,
    DeterministicExtraction,
    run_deterministic_extraction,
)
from apps.anonymization.engine import get_anonymization_engine, score_threshold, spacy_model_name
from apps.anonymization.operators import (
    CNS,
    CPF,
    DATA,
    NER_ENTITY_TYPE_TO_CATEGORY,
    OCORRENCIA,
    PESSOA,
    PII,
    PseudonymOperator,
)
from apps.cases.events import CaseEventType
from apps.cases.models import ActorType, Case, CaseEvent

SYSTEM_ROLE = "system"


@dataclass(frozen=True)
class SpanCandidate:
    """Span candidato à substituição (fonte determinística ou NER).

    ``deterministic=True`` marca origem determinística — vence o NER no empate
    de offsets da política de merge (R2/D6).
    """

    start: int
    end: int
    category: str
    deterministic: bool


@dataclass(frozen=True)
class AnonymizationCoreResult:
    """Resultado puro da anonimização de um texto (sem DB).

    ``extraction`` carrega o linkage (nome/nascimento/nº de ocorrência) que o
    wrapper persiste no ``Case``; o benchmark do slice 005 consome apenas
    ``anonymized_text``/``pseudonym_map``/``anonymization_report``.
    """

    anonymized_text: str
    pseudonym_map: dict[str, dict[str, str]]
    anonymization_report: dict[str, object]
    extraction: DeterministicExtraction


def anonymize_text(
    text: str,
    seed_map: Mapping[str, Mapping[str, str]] | None = None,
) -> AnonymizationCoreResult:
    """Núcleo puro da anonimização: passos 1–5 do design D6 (sem DB).

    ``seed_map`` (aditivo; default ``None`` preserva o fluxo atual) semeia o
    ``PseudonymOperator`` com o mapa do CASO (change attachment-processing-ocr,
    slice 003, D4): valores iguais aos de entidades do caso reutilizam o token
    do caso (o paciente do caso mantém o ``<PESSOA_N>`` do caso qualquer que
    seja a ordem no texto); valores novos ganham token NOVO numerado acima do
    máximo do seed. O mapa devolvido contém apenas entradas efetivamente usadas
    no texto (semeadas usadas + novas). O mapa do caso NÃO é alterado.

    Exceção de qualquer etapa propaga (fail-closed é fechado no worker do slice
    004). Texto vazio retorna resultado com zero entidades (defensivo; o
    porteiro que retém casos sem texto é a task do slice 004).
    """
    extraction = run_deterministic_extraction(text)
    if text == "":
        return AnonymizationCoreResult(
            anonymized_text="",
            pseudonym_map={},
            anonymization_report=_anonymization_report(Counter()),
            extraction=extraction,
        )

    engine = get_anonymization_engine()
    analyzer_results = engine.analyzer.analyze(
        text=text,
        language="pt",
        score_threshold=score_threshold(),
    )
    candidates = _deterministic_candidates(text, extraction)
    for result in analyzer_results:
        start = int(result.start)
        end = int(result.end)
        # Offsets sempre validados contra o texto (R2/D6): span inválido some.
        if 0 <= start < end <= len(text):
            category = NER_ENTITY_TYPE_TO_CATEGORY.get(result.entity_type, PII)
            candidates.append(SpanCandidate(start, end, category, deterministic=False))

    winners = merge_span_candidates(candidates)
    operator = PseudonymOperator(seed_map=seed_map)
    counts: Counter[str] = Counter()
    replacements: list[tuple[int, int, str]] = []
    for span in winners:
        token = operator.operate(text[span.start : span.end], span.category)
        counts[span.category] += 1
        replacements.append((span.start, span.end, token))

    return AnonymizationCoreResult(
        anonymized_text=_apply_replacements(text, replacements),
        pseudonym_map=operator.mapping,
        anonymization_report=_anonymization_report(counts),
        extraction=extraction,
    )


def anonymize_attachment_text(case: Case, text: str) -> AnonymizationCoreResult:
    """Anonimiza o texto de um anexo no espaço de tokens do caso (D4/R1).

    Wrapper ADITIVO do núcleo para anexos clínicos (change
    attachment-processing-ocr, slice 003): chama ``anonymize_text`` com
    ``seed_map=case.pseudonym_map`` (semeadura explícita — o paciente do caso
    mantém o token do caso; paciente diferente ganha token novo). Devolve o
    resultado puro (texto + mapa do ANEXO com apenas entradas efetivamente
    usadas); a persistência na row do anexo é responsabilidade da task
    (``apps/attachments/tasks.py``). O mapa e o texto do caso não são tocados.
    """
    return anonymize_text(text, seed_map=case.pseudonym_map)


def anonymize_case_text(case: Case) -> AnonymizationCoreResult:
    """Wrapper transacional: anonimiza e persiste artefatos + evento (R4).

    Numa única transação grava em ``case``: ``anonymized_text``,
    ``pseudonym_map``, ``anonymization_report`` e o linkage determinístico —
    ``patient_name``/``patient_birth_date`` SEMPRE sobrescritos pelo resultado
    do núcleo (em reprocessamento, sumiu do texto → vira vazio; ``None`` → campo
    vazio) e ``agency_record_number`` apenas quando o núcleo achou e o campo
    ainda está vazio (único campo preservado-se-vazio, contrato R4) — +
    ``CaseEvent`` ``CASE_ANONYMIZATION_COMPLETED``. NÃO faz
    transição de estado (a task do slice 004 envolve este wrapper e o
    ``complete_anonymization`` no mesmo ``transaction.atomic()``). Exceção em
    qualquer etapa propaga — nada é escrito (fail-closed no worker).
    """
    with transaction.atomic():
        result = anonymize_text(case.extracted_text)
        case.anonymized_text = result.anonymized_text
        case.pseudonym_map = result.pseudonym_map
        case.anonymization_report = result.anonymization_report
        case.patient_name = result.extraction.patient_name or ""
        case.patient_birth_date = result.extraction.birth_date
        if result.extraction.record_number is not None and not case.agency_record_number:
            case.agency_record_number = result.extraction.record_number
        case.save()
        report = result.anonymization_report
        CaseEvent.objects.create(
            case=case,
            event_type=CaseEventType.CASE_ANONYMIZATION_COMPLETED,
            actor_type=ActorType.SYSTEM,
            actor=None,
            actor_role=SYSTEM_ROLE,
            payload={
                "counts_by_type": report["counts_by_type"],
                "model": report["model"],
                "presidio_analyzer_version": report["presidio_analyzer_version"],
                "presidio_anonymizer_version": report["presidio_anonymizer_version"],
            },
        )
    return result


def merge_span_candidates(candidates: Iterable[SpanCandidate]) -> list[SpanCandidate]:
    """Merge com a política determinística completa (R2/D6).

    Ordena por ``(start asc, end desc, determinístico > NER)`` e resolve
    sobreposições mantendo o primeiro da ordem — mais à esquerda, mais largo,
    determinístico vence; spans descartados somem. Retorna os vencedores já em
    ordem de offset (a numeração de tokens segue essa ordem).
    """
    ordered = sorted(
        candidates,
        key=lambda span: (span.start, -span.end, 0 if span.deterministic else 1),
    )
    kept: list[SpanCandidate] = []
    for span in ordered:
        if any(span.start < other.end and other.start < span.end for other in kept):
            continue
        kept.append(span)
    return kept


# ── Passo 1: spans determinísticos (todas as ocorrências, offsets válidos) ─


def _deterministic_candidates(
    text: str, extraction: DeterministicExtraction
) -> list[SpanCandidate]:
    """TODAS as ocorrências de cada valor determinístico no texto (R2).

    Cada valor vira spans com a categoria própria mesmo sem NER: nome→PESSOA,
    nascimento→DATA, nº de ocorrência→OCORRENCIA, CPF→CPF, CNS→CNS. Os spans de
    CPF/CNS reusam a varredura de runs de ``deterministic`` (mesmo contrato de
    boundary: um run só é ocorrência quando normaliza EXATAMENTE para o tamanho
    do candidato), preservando a paridade com a pré-extração.
    """
    candidates: list[SpanCandidate] = []
    if extraction.patient_name is not None:
        for start, end in _find_folded_occurrences(text, extraction.patient_name):
            candidates.append(SpanCandidate(start, end, PESSOA, deterministic=True))
    if extraction.birth_date is not None:
        birth = extraction.birth_date
        for start, end in _find_date_occurrences(text, birth.day, birth.month, birth.year):
            candidates.append(SpanCandidate(start, end, DATA, deterministic=True))
    if extraction.record_number is not None:
        for start, end in _find_literal_occurrences(text, extraction.record_number):
            candidates.append(SpanCandidate(start, end, OCORRENCIA, deterministic=True))
    cpf_digits = set(extraction.cpf_candidates)
    cns_digits = set(extraction.cns_candidates)
    for match in _DIGIT_RUN_PATTERN.finditer(text):
        digits = re.sub(r"[^0-9]", "", match.group(0))
        if digits in cpf_digits:
            candidates.append(SpanCandidate(match.start(), match.end(), CPF, deterministic=True))
        elif digits in cns_digits:
            candidates.append(SpanCandidate(match.start(), match.end(), CNS, deterministic=True))
    return candidates


def _find_folded_occurrences(text: str, value: str) -> list[tuple[int, int]]:
    """Ocorrências de um nome (case-insensitive, whitespace flexível).

    O valor extraído pode ter linhas de continuação recompostas com espaço (D2);
    no texto a quebra é outra — entre tokens qualquer whitespace casa. Bordas de
    palavra impedem casar o nome dentro de palavras maiores.
    """
    tokens = [re.escape(token) for token in value.split() if token]
    if not tokens:
        return []
    pattern = re.compile(r"(?<!\w)" + r"\s+".join(tokens) + r"(?!\w)", re.IGNORECASE)
    return [match.span() for match in pattern.finditer(text)]


def _find_date_occurrences(text: str, day: int, month: int, year: int) -> list[tuple[int, int]]:
    """Ocorrências de uma data dd/mm/aaaa no texto, em qualquer formatação.

    O rótulo original pode usar dia/mês com ou sem zero à esquerda e separador
    ``/`` ou ``-`` (todos aceitos pela pré-extração) — todas as variantes são
    localizadas e substituídas.
    """
    days = {str(day), f"{day:02d}"}
    months = {str(month), f"{month:02d}"}
    forms = [
        f"{d}{sep}{m}{sep}{year}"
        for d in sorted(days)
        for m in sorted(months)
        for sep in ("/", "-")
    ]
    pattern = re.compile(r"(?<!\d)(?:" + "|".join(re.escape(form) for form in forms) + r")(?!\d)")
    return [match.span() for match in pattern.finditer(text)]


def _find_literal_occurrences(text: str, value: str) -> list[tuple[int, int]]:
    """Ocorrências literais de um valor com bordas de dígito (nº de ocorrência)."""
    pattern = re.compile(r"(?<!\d)" + re.escape(value) + r"(?!\d)")
    return [match.span() for match in pattern.finditer(text)]


# ── Passo 4/5: substituição + artefatos ───────────────────────────────────


def _apply_replacements(text: str, replacements: list[tuple[int, int, str]]) -> str:
    """Aplica as substituições de trás para frente (offsets anteriores intactos)."""
    result = text
    for start, end, token in sorted(replacements, reverse=True):
        result = result[:start] + token + result[end:]
    return result


def _anonymization_report(counts: Counter[str]) -> dict[str, object]:
    """Relatório JSON: contagens por tipo + modelo + versões + threshold (R4)."""
    return {
        "counts_by_type": dict(counts),
        "model": spacy_model_name(),
        "score_threshold": score_threshold(),
        "presidio_analyzer_version": importlib.metadata.version("presidio-analyzer"),
        "presidio_anonymizer_version": importlib.metadata.version("presidio-anonymizer"),
    }
