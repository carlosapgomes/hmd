"""Harness de benchmark de anonimização (slice 005, change presidio-anonymization;
design D9/R2; requirement spec "Benchmark como critério de aceite").

Lê um corpus JSONL — cada linha ``{"text": ..., "expected": [{"value": ...,
"entity_type": ...}]}`` — e, para cada entrada, roda o NÚCLEO puro
``anonymize_text`` (slice 003 — sem caso/DB). Mede: recall por tipo de
entidade esperada (valor esperado ausente do output = acerto), contagens por
tipo detectado (tokens no output; o relatório soma os TOTAIS esperadas/
detectadas além do por-tipo), latência por documento (p50/p95), varredura
zero-PII no output (CPF/CNS com checksum em DUAS camadas: runs contíguos de
dígitos com janelas deslizantes 11/15 — detecta CPF/CNS colados a outros
dígitos que o pipeline não anonimiza — e padrões FORMATADOS normalizados p/
dígitos antes do checksum, ex. ``955.075.869-93``, que a camada de runs
fragmentaria sem nunca validar), pico de RSS do processo
(``resource.getrusage``) e documentos bloqueados (erro de anonimização na
entrada = fail-closed durante o benchmark).

Exit 0 apenas se todo tipo com entidades esperadas tiver recall ≥ mínimo
(``--min-recall``; default do setting ``ANONYMIZATION_BENCHMARK_MIN_RECALL``,
0.90) E a varredura zero-PII estiver limpa E zero documentos bloqueados E o
pico de RSS não exceder o limite opcional
``ANONYMIZATION_BENCHMARK_MAX_RSS_MB`` (default desligado). Corpus sintético
versionado roda na suíte (``apps/anonymization/tests/fixtures/benchmark_corpus.jsonl``);
a aceitação com corpus REAL é operacional, pré-produção (fora do CI — README).

Semântica do setting ``ANONYMIZATION_USE_NER`` (change
anonymization-deterministic-first, D2): LIGADO, calibra a camada COMPLETA
(determinística + NER) — é o modo do corpus padrão, que exige o NER no recall
de CRM (categoria EXCLUSIVA do recognizer brasileiro); DESLIGADO (default da
fase 2), mede o baseline determinístico-only (a perda de recall de terceiros
antes de reativar). O comando NÃO força o setting: o modo vem do ambiente
(env/``override_settings`` na suíte) e a reativação segue o README.
"""

from __future__ import annotations

import json
import math
import re
import resource
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.anonymization import services
from apps.anonymization.recognizers import only_digits, valid_cns, valid_cpf

# Default absoluto do recall mínimo (usado apenas se o setting não existir).
_DEFAULT_MIN_RECALL = 0.90
# Token de pseudônimo no output: ``<CATEGORIA_N>`` (categorias canônicas do
# operador do slice 003; sem prefixos comuns — o regex não cruza categorias).
_TOKEN_PATTERN = re.compile(r"<([A-Z_]+)_\d+>")

# Camada 2 da varredura zero-PII (review P1): padrões FORMATADOS de CPF/CNS,
# com os separadores usuais entre os blocos de 3 dígitos. Sem esta camada um
# ``955.075.869-93`` no output viraria runs 955/075/869/93 e nunca validaria
# como CPF. O candidato é normalizado para dígitos antes do checksum
# (valid_cpf/valid_cns); candidatos sem separador são runs contíguos puros que
# a camada 1 (janelas deslizantes) já cobre — sem duplicar o achado.
_FORMATTED_CPF_PATTERN = re.compile(r"\d{3}[.\- ]?\d{3}[.\- ]?\d{3}[\- ]?\d{2}")
_FORMATTED_CNS_PATTERN = re.compile(r"\d{3}[.\- ]?\d{3}[.\- ]?\d{3}[.\- ]?\d{3}[.\- ]?\d{3}")


@dataclass(frozen=True)
class _ExpectedEntity:
    """Entidade esperada de uma entrada do corpus (R2)."""

    entity_type: str
    value: str


@dataclass(frozen=True)
class _CorpusEntry:
    """Uma entrada do corpus JSONL (texto + entidades esperadas)."""

    text: str
    expected: tuple[_ExpectedEntity, ...]


def _percentile(sorted_values: Sequence[float], percentage: float) -> float:
    """Percentil ``percentage`` (0–100) de valores ordenados (p50/p95, R2)."""
    if not sorted_values:
        return float("nan")
    index = max(0, math.ceil(len(sorted_values) * percentage / 100.0) - 1)
    return sorted_values[index]


def _scan_pii_leaks(text: str) -> list[tuple[str, str]]:
    """Varredura zero-PII do output: CPF/CNS válidos por checksum (R2/D9).

    DUAS camadas complementares (review P1):
    (a) janelas deslizantes de 11/15 dígitos dentro de cada run CONTÍGUO de
    dígitos — detecta um CPF/CNS que sobreviveu à anonimização mesmo colado a
    outros dígitos (run > 11/15 — o caminho do pipeline não o anonimiza, mas a
    varredura o encontra);
    (b) padrões FORMATADOS de CPF/CNS (separadores ``.``/``-``/espaço entre os
    blocos), com normalização para dígitos ANTES de ``valid_cpf``/``valid_cns``
    — cobre o caso de ``955.075.869-93`` no output, que a camada (a)
    fragmentaria em runs 955/075/869/93 sem nunca validar como CPF/CNS.
    Candidatos (b) puramente numéricos (sem separador) são runs contíguos que
    a camada (a) já varre — não duplicam o achado.

    Os validadores rejeitam sequências repetidas ("111.111.111-11"), evitando
    falsos positivos em runs de marca d'água.
    """
    leaks: list[tuple[str, str]] = []
    # Camada (a): janelas deslizantes dentro de cada run contíguo de dígitos.
    for run in re.findall(r"[0-9]+", text):
        for start in range(max(0, len(run) - 10)):
            window = run[start : start + 11]
            if valid_cpf(window):
                leaks.append(("CPF", window))
        for start in range(max(0, len(run) - 14)):
            window = run[start : start + 15]
            if valid_cns(window):
                leaks.append(("CNS", window))
    # Camada (b): padrões formatados — normaliza p/ dígitos antes do checksum.
    for match in _FORMATTED_CPF_PATTERN.finditer(text):
        candidate = match.group(0)
        digits = only_digits(candidate)
        if len(digits) < len(candidate) and valid_cpf(digits):
            leaks.append(("CPF", digits))
    for match in _FORMATTED_CNS_PATTERN.finditer(text):
        candidate = match.group(0)
        digits = only_digits(candidate)
        if len(digits) < len(candidate) and valid_cns(digits):
            leaks.append(("CNS", digits))
    return leaks


class Command(BaseCommand):
    help = (
        "Benchmark de anonimização sobre um corpus JSONL: recall por tipo, "
        "contagens, latência p50/p95, varredura zero-PII, RSS e documentos "
        "bloqueados. Exit != 0 abaixo do recall mínimo, com vestígio de PII, "
        "documento bloqueado ou RSS acima do limite. Mede a camada ativa: com "
        "ANONYMIZATION_USE_NER ligado calibra o NER (corpus padrão exige NER "
        "para o CRM); desligado (default) mede o baseline determinístico-only."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--corpus",
            required=True,
            help=(
                "Caminho do corpus JSONL: uma entrada por linha "
                '{"text": ..., "expected": [{"value": ..., "entity_type": ...}]}'
            ),
        )
        parser.add_argument(
            "--min-recall",
            type=float,
            default=None,
            help=(
                "Recall mínimo por tipo para o benchmark passar (default: "
                "setting ANONYMIZATION_BENCHMARK_MIN_RECALL = 0.90)"
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        corpus_path = Path(str(options["corpus"]))
        if not corpus_path.is_file():
            raise CommandError(f"corpus não encontrado: {corpus_path}")
        entries = _load_corpus(corpus_path)
        if not entries:
            raise CommandError(f"corpus vazio: {corpus_path}")

        min_recall = _resolve_min_recall(options["min_recall"])
        max_rss_mb = getattr(settings, "ANONYMIZATION_BENCHMARK_MAX_RSS_MB", None)

        # Acumuladores do relatório (R2).
        expected_total: Counter[str] = Counter()
        expected_hits: Counter[str] = Counter()
        detected_counts: Counter[str] = Counter()
        latencies_ms: list[float] = []
        pii_leaks: list[tuple[int, str, str]] = []
        blocked: list[tuple[int, str]] = []

        for index, entry in enumerate(entries, start=1):
            start = time.perf_counter()
            try:
                result = services.anonymize_text(entry.text)
                output = result.anonymized_text
                detected_counts.update(_TOKEN_PATTERN.findall(output))
                for entity in entry.expected:
                    expected_total[entity.entity_type] += 1
                    # Acerto: o valor esperado não está no output (D9).
                    if entity.value not in output:
                        expected_hits[entity.entity_type] += 1
                for kind, value in _scan_pii_leaks(output):
                    pii_leaks.append((index, kind, value))
            except Exception as exc:
                # Bloqueio (fail-closed): a entrada não derruba o relatório,
                # mas QUALQUER documento bloqueado reprova o comando.
                blocked.append((index, str(exc)))
                continue
            finally:
                latencies_ms.append((time.perf_counter() - start) * 1000.0)

        latencies_ms.sort()
        rss_mb = _peak_rss_mb()
        failures = _collect_failures(
            min_recall=min_recall,
            max_rss_mb=max_rss_mb,
            expected_total=expected_total,
            expected_hits=expected_hits,
            pii_leaks=pii_leaks,
            blocked=blocked,
            rss_mb=rss_mb,
        )
        report = self._build_report(
            corpus_path=corpus_path,
            entries_count=len(entries),
            blocked=blocked,
            expected_total=expected_total,
            expected_hits=expected_hits,
            detected_counts=detected_counts,
            latencies_ms=latencies_ms,
            pii_leaks=pii_leaks,
            rss_mb=rss_mb,
            max_rss_mb=max_rss_mb,
            failures=failures,
        )
        self.stdout.write(report)
        if failures:
            raise CommandError("benchmark reprovado: " + "; ".join(failures))

    def _build_report(
        self,
        *,
        corpus_path: Path,
        entries_count: int,
        blocked: list[tuple[int, str]],
        expected_total: Counter[str],
        expected_hits: Counter[str],
        detected_counts: Counter[str],
        latencies_ms: list[float],
        pii_leaks: list[tuple[int, str, str]],
        rss_mb: float,
        max_rss_mb: int | None,
        failures: list[str],
    ) -> str:
        lines = [
            "Benchmark de anonimização",
            f"Corpus: {corpus_path}",
            f"Entradas: {entries_count} | Bloqueadas: {len(blocked)}",
            "Recall por tipo (acertos/esperados):",
        ]
        if expected_total:
            for entity_type in sorted(expected_total):
                total = expected_total[entity_type]
                hits = expected_hits[entity_type]
                lines.append(f"  {entity_type}: {hits}/{total} = {hits / total:.3f}")
        else:
            lines.append("  (nenhuma entidade esperada no corpus)")
        lines.append("Contagens por tipo (tokens no output):")
        if detected_counts:
            for entity_type in sorted(detected_counts):
                lines.append(f"  {entity_type}: {detected_counts[entity_type]}")
        else:
            lines.append("  (nenhum token no output)")
        # Totais agregados do corpus além dos por-tipo (review P2).
        lines.append(
            "TOTAL: "
            f"{sum(expected_total.values())} esperadas / "
            f"{sum(detected_counts.values())} detectadas"
        )
        p50 = _percentile(latencies_ms, 50)
        p95 = _percentile(latencies_ms, 95)
        lines.append(
            f"Latência por documento (ms): p50={p50:.1f} p95={p95:.1f} (n={len(latencies_ms)})"
        )
        if max_rss_mb:
            limit_text = f"{max_rss_mb} MB"
        else:
            limit_text = "desligado"
        lines.append(f"RSS pico: {rss_mb:.1f} MB (limite: {limit_text})")
        if pii_leaks:
            leaks_text = "; ".join(
                f"{kind}={value} (entrada {index})" for index, kind, value in pii_leaks
            )
            lines.append(f"Zero-PII: violação zero-PII: {leaks_text}")
        else:
            lines.append("Zero-PII: limpo")
        if blocked:
            blocked_text = "; ".join(f"entrada {index} ({reason})" for index, reason in blocked)
            lines.append(f"Documentos bloqueados: {len(blocked)} ({blocked_text})")
        else:
            lines.append("Documentos bloqueados: 0")
        for failure in failures:
            lines.append(f"Falha: {failure}")
        lines.append("Resultado: PASS" if not failures else "Resultado: FAIL")
        return "\n".join(lines)


def _resolve_min_recall(min_recall: float | None) -> float:
    """Recall mínimo: argumento ``--min-recall`` ou setting do Django."""
    if min_recall is None:
        min_recall = float(
            getattr(settings, "ANONYMIZATION_BENCHMARK_MIN_RECALL", _DEFAULT_MIN_RECALL)
        )
    if not 0.0 <= min_recall <= 1.0:
        raise CommandError("--min-recall deve estar entre 0 e 1.")
    return min_recall


def _peak_rss_mb() -> float:
    """Pico de RSS do processo em MB (ru_maxrss; KiB no Linux)."""
    rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss_kib / 1024.0


def _collect_failures(
    *,
    min_recall: float,
    max_rss_mb: int | None,
    expected_total: Counter[str],
    expected_hits: Counter[str],
    pii_leaks: list[tuple[int, str, str]],
    blocked: list[tuple[int, str]],
    rss_mb: float,
) -> list[str]:
    """Motivos de reprovação do benchmark (vazio = passa, R2/D9)."""
    failures: list[str] = []
    for entity_type in sorted(expected_total):
        recall = expected_hits[entity_type] / expected_total[entity_type]
        if recall < min_recall:
            failures.append(
                f"recall abaixo do mínimo — {entity_type}: recall {recall:.3f} < {min_recall:.3f}"
            )
    for index, kind, value in pii_leaks:
        failures.append(f"violação zero-PII: {kind}={value} (entrada {index})")
    for index, reason in blocked:
        failures.append(f"documento bloqueado: entrada {index} ({reason})")
    if max_rss_mb and rss_mb > max_rss_mb:
        failures.append(f"RSS acima do limite: {rss_mb:.1f} MB > {max_rss_mb} MB")
    return failures


def _load_corpus(path: Path) -> list[_CorpusEntry]:
    """Carrega e valida o corpus JSONL (R2): texto + entidades esperadas."""
    entries: list[_CorpusEntry] = []
    try:
        handle = path.open(encoding="utf-8")
    except OSError as exc:
        raise CommandError(f"não foi possível ler o corpus {path}: {exc}") from exc
    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CommandError(
                    f"JSON inválido na linha {line_number} do corpus: {exc}"
                ) from exc
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("text"), str)
                or not isinstance(data.get("expected"), list)
            ):
                raise CommandError(
                    f"entrada {line_number} do corpus inválida: esperado "
                    '{"text": str, "expected": list}'
                )
            expected: list[_ExpectedEntity] = []
            for item_index, item in enumerate(data["expected"]):
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("value"), str)
                    or not isinstance(item.get("entity_type"), str)
                ):
                    raise CommandError(
                        f"entrada {line_number} do corpus: item {item_index + 1} de "
                        '"expected" inválido (esperado {"value": str, '
                        '"entity_type": str})'
                    )
                expected.append(
                    _ExpectedEntity(entity_type=item["entity_type"], value=item["value"])
                )
            entries.append(_CorpusEntry(text=data["text"], expected=tuple(expected)))
    return entries
