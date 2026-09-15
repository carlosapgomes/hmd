"""Testes do harness de benchmark de anonimização (slice 005, change
presidio-anonymization; design D9/R2-R4; requirement spec "Benchmark como
critério de aceite").

Rodam o comando real ``anonymization_benchmark`` via ``call_command`` e
carregam o modelo spaCy pt (lentos, ok na suíte — o singleton do engine é
compartilhado no processo). Cenários da spec: o corpus sintético versionado
(``fixtures/benchmark_corpus.jsonl``) passa no CI (exit 0, recall ≥ mínimo e
zero-PII limpo); corpus adversário (entidade que o pipeline não pega — nome
exótico sem span determinístico e NER falha) reprova com o relatório apontando
o tipo; CPF válido injetado que passa sem detecção reprova na varredura
zero-PII; CPF/CNS FORMATADOS sobrevivendo no output (que a camada de runs
contíguos fragmentaria sem validar) também reprovam — a varredura zero-PII é
em DUAS camadas (review P1); o relatório traz os totais agregados
esperadas/detectadas além do por-tipo (review P2); documento bloqueado (erro
de anonimização) reprova.
"""

from __future__ import annotations

import io
import json
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.anonymization import services

# Corpus sintético versionado (R3): dados fabricados — nomes fictícios,
# CPF/CNS válidos gerados algoritmicamente, labels SESAB; sem PII real.
_CORPUS_FIXTURE = Path(__file__).parent / "fixtures" / "benchmark_corpus.jsonl"

# Nome exótico FABRICADO que o pipeline atual não detecta (probe empírico com o
# modelo real: sem rótulo determinístico e sem span de NER — a calibração de
# recognizers é pós-benchmark, fora do escopo deste slice).
_ADVERSARIAL_NAME = "MNQTX ZKWRV"


def _run_benchmark(corpus: Path, *, min_recall: float | None = None) -> str:
    """Executa o comando e devolve o relatório impresso (stdout capturado).

    O CommandError do comando (exit ≠ 0) PROPAGA após o relatório completo ter
    sido escrito no buffer — o teste lê o relatório no except.
    """
    out = io.StringIO()
    options: dict[str, Any] = {"corpus": str(corpus), "stdout": out}
    if min_recall is not None:
        options["min_recall"] = min_recall
    call_command("anonymization_benchmark", **options)
    return out.getvalue()


def _write_corpus(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    """Grava um corpus JSONL efêmero para os cenários de reprovação."""
    path = tmp_path / "corpus.jsonl"
    payload = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
    path.write_text(payload, encoding="utf-8")
    return path


def _identity_output(text: str) -> Any:
    """Stub do núcleo: devolve o texto de entrada como output (nada anonimiza).

    Usado nos casos adversários de zero-PII formatado — o pipeline real
    anonimizaria um CPF/CNS formatado válido; o cenário é o valor sobrevivendo
    SEM anonimização no output da varredura.
    """
    return SimpleNamespace(anonymized_text=text)


# ── Spec: "Corpus sintético passa no CI" ──────────────────────────────────


@override_settings(ANONYMIZATION_USE_NER=True)
def test_synthetic_corpus_passes() -> None:
    """R4/cenário spec: o corpus sintético versionado passa (exit 0).

    Recall por tipo ≥ mínimo (0.90), zero-PII limpo e zero documentos
    bloqueados → ``Resultado: PASS`` e nenhuma exceção do comando. O override
    do setting documenta a intenção: o corpus exige o CRM, categoria EXCLUSIVA
    do recognizer NER — o benchmark calibra a camada opt-in.
    """
    report = _run_benchmark(_CORPUS_FIXTURE)

    assert "Resultado: PASS" in report
    assert "Recall por tipo" in report
    assert "Zero-PII: limpo" in report
    # Totais agregados do relatório além do por-tipo (P2): as 13 entradas do
    # corpus esperam 63 valores no total (55 das 10 originais + 3 + 3 + 2 das
    # três entradas do layout real do cabeçalho SESAB).
    assert "TOTAL: 63 esperadas / " in report


@override_settings(ANONYMIZATION_USE_NER=False)
def test_sesab_header_layout_recall_deterministic_only(tmp_path: Path) -> None:
    """R4/D7: entradas do layout real passam com o NER DESLIGADO.

    Isola a âncora determinística do cabeçalho (nome desalinhado na linha de
    demografia, antes do rótulo ``Paciente:`` sozinho): sem ela os nomes
    sobrevivem ao output e o recall de PESSOA reprova — com o NER ligado o
    modelo poderia mascarar a falha.
    """
    entries = [
        json.loads(line)
        for line in _CORPUS_FIXTURE.read_text(encoding="utf-8").splitlines()
        if "Paciente:\n" in json.loads(line)["text"]
    ]
    assert len(entries) == 3  # as três entradas do layout real do cabeçalho
    corpus = _write_corpus(tmp_path, entries)

    report = _run_benchmark(corpus)

    assert "PESSOA: 4/4 = 1.000" in report
    assert "OCORRENCIA: 3/3 = 1.000" in report
    assert "Zero-PII: limpo" in report
    assert "Resultado: PASS" in report


# ── Spec: "Benchmark reprova corpus abaixo do mínimo" ─────────────────────


@override_settings(ANONYMIZATION_USE_NER=True)
def test_adversarial_corpus_fails(tmp_path: Path) -> None:
    """R4: entidade que o pipeline não pega → exit ≠ 0 apontando o tipo.

    O corpus tem uma única entrada com um nome exótico FABRICADO esperado como
    PESSOA que não tem rótulo determinístico e que o NER não detecta: o valor
    permanece no output → recall de PESSOA = 0 < mínimo → o comando reprova e o
    relatório aponta o tipo reprovado. O override mantém o cenário no caminho
    NER (é a falha do NER que o teste pina) e não no determinístico-only.
    """
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "text": (
                    "Resumo Clínico: avaliação de paciente identificado como "
                    f"{_ADVERSARIAL_NAME} no encaminhamento recebido da unidade de origem.\n"
                ),
                "expected": [{"value": _ADVERSARIAL_NAME, "entity_type": "PESSOA"}],
            }
        ],
    )

    report = io.StringIO()
    with pytest.raises(CommandError):
        call_command("anonymization_benchmark", corpus=str(corpus), stdout=report)

    output = report.getvalue()
    assert "Resultado: FAIL" in output
    assert "PESSOA" in output  # relatório aponta o tipo reprovado
    assert "recall abaixo do mínimo" in output
    assert "TOTAL: 1 esperadas / 0 detectadas" in output  # totais agregados (P2)


def test_adversarial_corpus_recall_threshold(tmp_path: Path) -> None:
    """R4: mesmo corpus com recall mínimo 0 (abaixo do default 0.90) passa.

    O limiar é configuração: com ``--min-recall 0`` o mesmo corpus adversário
    não reprova por recall (contraprova de que a reprovação acima é do recall,
    não de outro critério).
    """
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "text": (
                    "Resumo Clínico: avaliação de paciente identificado como "
                    f"{_ADVERSARIAL_NAME} no encaminhamento recebido da unidade de origem.\n"
                ),
                "expected": [{"value": _ADVERSARIAL_NAME, "entity_type": "PESSOA"}],
            }
        ],
    )

    report = _run_benchmark(corpus, min_recall=0.0)

    assert "Resultado: PASS" in report
    assert "TOTAL: 1 esperadas / 0 detectadas" in report  # totais agregados (P2)


# ── R4: varredura zero-PII reprova ────────────────────────────────────────


def test_zero_pii_violation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R4: CPF válido que passa sem detecção no output → exit ≠ 0.

    Um CPF válido (gerado) colado a um dígito prefixo vira um run de 12 dígitos
    que o pipeline não anonimiza (nem o recognizer nem o caminho determinístico
    casam dentro do run); a varredura zero-PII do output detecta a janela de 11
    dígitos com checksum válido e reprova o comando.
    """
    cpf = _generated_cpf_digits(seed=501)
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "text": (
                    "RELATÓRIO DE OCORRÊNCIAS\n"
                    "Código: 33345\n"
                    "Resumo Clínico: prontuário 0"
                    f"{cpf} anexado ao processo de regulação.\n"
                ),
                "expected": [],
            }
        ],
    )

    report = io.StringIO()
    with pytest.raises(CommandError):
        call_command("anonymization_benchmark", corpus=str(corpus), stdout=report)

    output = report.getvalue()
    assert "Resultado: FAIL" in output
    assert "violação zero-PII" in output
    assert "CPF" in output
    # Totais agregados (P2): sem entidades esperadas, o total de esperadas é 0
    # (as detectadas dependem do que o pipeline ainda tokeniza no documento).
    assert "TOTAL: 0 esperadas / " in output


def test_zero_pii_formatted_cpf_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P1 (review): CPF FORMATADO no output → exit ≠ 0 (varredura em 2 camadas).

    A camada de runs contíguos fragmenta ``955.075.869-93`` em runs
    955/075/869/93 e nunca o valida como CPF; a camada de padrões formatados
    (normalização para dígitos + checksum) precisa reprovar. O stub devolve o
    texto intacto como output — o pipeline real anonimizaria um CPF formatado
    válido; o caso adversário é o valor sobrevivendo sem anonimização.
    """
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "text": (
                    "RELATÓRIO DE OCORRÊNCIAS\n"
                    "Código: 33345\n"
                    "CPF: 955.075.869-93\n"
                    "Resumo Clínico: encaminhamento com CPF visível no output.\n"
                ),
                "expected": [],
            }
        ],
    )
    monkeypatch.setattr(services, "anonymize_text", _identity_output)

    report = io.StringIO()
    with pytest.raises(CommandError):
        call_command("anonymization_benchmark", corpus=str(corpus), stdout=report)

    output = report.getvalue()
    assert "Resultado: FAIL" in output
    assert "violação zero-PII" in output
    assert "CPF=95507586993" in output


def test_zero_pii_formatted_cns_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P1 (review): CNS FORMATADO no output → exit ≠ 0 (varredura em 2 camadas).

    Mesmo caso do CPF formatado: separadores entre os blocos fragmentariam a
    camada de runs; a camada de padrões formatados valida o valor por checksum
    (normalizado para dígitos) e reprova o comando.
    """
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "text": (
                    "RELATÓRIO DE OCORRÊNCIAS\n"
                    "CNS: 547 762 666 886 141\n"
                    "Resumo Clínico: encaminhamento com CNS visível no output.\n"
                ),
                "expected": [],
            }
        ],
    )
    monkeypatch.setattr(services, "anonymize_text", _identity_output)

    report = io.StringIO()
    with pytest.raises(CommandError):
        call_command("anonymization_benchmark", corpus=str(corpus), stdout=report)

    output = report.getvalue()
    assert "Resultado: FAIL" in output
    assert "violação zero-PII" in output
    assert "CNS=547762666886141" in output


# ── R4: documento bloqueado reprova ───────────────────────────────────────


def test_blocked_document_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R4: erro de anonimização durante o benchmark → exit ≠ 0 (fail-closed).

    O pipeline do benchmark trata cada entrada isoladamente (bloqueio não
    derruba o relatório), mas QUALQUER documento bloqueado reprova o comando
    com a contagem e o motivo no relatório.
    """
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "text": "Resumo Clínico: documento com falha induzida BLOQUEAR.\n",
                "expected": [],
            }
        ],
    )

    def _exploding(text: str) -> Any:
        if "BLOQUEAR" in text:
            raise RuntimeError("falha induzida no documento")
        return services.anonymize_text(text)

    monkeypatch.setattr(services, "anonymize_text", _exploding)

    report = io.StringIO()
    with pytest.raises(CommandError):
        call_command("anonymization_benchmark", corpus=str(corpus), stdout=report)

    output = report.getvalue()
    assert "Resultado: FAIL" in output
    assert "documento bloqueado" in output
    assert "falha induzida no documento" in output
    assert "TOTAL: 0 esperadas / 0 detectadas" in output  # totais agregados (P2)


def _generated_cpf_digits(seed: int) -> str:
    """CPF válido de 11 dígitos (mesmo algoritmo das fixtures do app)."""
    rng = random.Random(seed)
    base = [rng.randrange(10) for _ in range(9)]
    while len(set(base)) == 1:
        base = [rng.randrange(10) for _ in range(9)]

    def _check_digit(digits: list[int], position: int) -> int:
        total = sum(digits[index] * (position + 1 - index) for index in range(position))
        expected = (total * 10) % 11
        return 0 if expected == 10 else expected

    first = _check_digit(base, 9)
    second = _check_digit(base + [first], 10)
    return "".join(str(digit) for digit in base + [first, second])
