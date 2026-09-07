"""Pré-extração determinística de identificadores (slice 001, change
presidio-anonymization; design D2, requirement "Pré-extração determinística").

Funções puras (regex, zero I/O, zero modelo) sobre o texto extraído do
relatório SESAB: nº de ocorrência — REUSO por import do padrão de
``apps.intake.pdf_utils`` (sem duplicação) —, nome do paciente, data de
nascimento e candidatos CPF/CNS por regex. CPF/CNS aqui são apenas CANDIDATOS
normalizados (dígitos, sem pontuação): a validação por checksum vive nos
recognizers do slice 002. Saída congelável em ``DeterministicExtraction``
(R1/R6); nome e nascimento alimentam os campos de linkage do ``Case`` no
slice 003 e o nº de ocorrência usa o campo existente (change 04).

Semântica de campos: o valor de um rótulo começa após o rótulo e vai até a
quebra do campo seguinte — a próxima linha que inicia um rótulo canônico do
relatório (espelho das seções operacionais do gate em
``apps/intake/regulation_gate.py`` + rótulos de identificação do paciente) —
ou o fim do texto. Linhas de continuação (nome longo quebrado pelo PDF) são
recompostas no valor.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from apps.intake.pdf_utils import extract_agency_record_number

# ── Rótulos canônicos de campo do relatório SESAB (quebra de valor) ───────
# Minúsculos/sem acento (a comparação é sobre linhas normalizadas por fold):
# rótulos de identificação do paciente + cabeçalho/sinais institucionais
# (repetidos página a página) + seções operacionais reconhecidas pelo gate.
_FIELD_BREAK_LABELS: tuple[str, ...] = (
    # Identificação do paciente (rótulos de nome e nascimento — R3/R4).
    "nome do paciente",
    "paciente",
    "nome",
    "data de nascimento",
    "nascimento",
    "cpf",
    "cns",
    # Cabeçalho e sinais institucionais do relatório.
    "relatorio de ocorrencias",
    "central estadual de regulacao",
    "secretaria da saude do estado",
    "governo do estado da bahia",
    # Seções operacionais reconhecidas pelo gate (apps/intake/regulation_gate.py).
    "codigo",
    "abertura",
    "unid. origem",
    "unidade de origem",
    "motivo da solicitacao",
    "complemento da solicitacao",
    "resumo clinico",
    "dias em tela",
    "data adm. unid.",
)

_FIELD_BREAK_PATTERN = re.compile(
    r"^(?:" + "|".join(re.escape(label) for label in _FIELD_BREAK_LABELS) + r")(?:\s*:|\s*$)"
)

# Rótulos de nome do paciente (R3): case-insensitive, espaços flexíveis no
# rótulo e antes/depois dos dois-pontos.
_PATIENT_NAME_FIELD_PATTERN = re.compile(
    r"^\s*(?:Nome\s+do\s+Paciente|Paciente|Nome)\s*:\s*",
    flags=re.IGNORECASE,
)

# Rótulos de nascimento (R4); o rótulo pode aparecer no meio da linha (a data
# é curta e logo após o rótulo).
_BIRTH_DATE_FIELD_PATTERN = re.compile(
    r"(?:Data\s+de\s+Nascimento|Nascimento)\s*:\s*",
    flags=re.IGNORECASE,
)
_DATE_PATTERN = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")

# Candidatos CPF/CNS (11/15 dígitos, sem checksum neste slice): em vez de
# lookarounds sobre dígitos adjacentes, varre-se o RUN completo de dígitos +
# separadores (espaço/ponto/hífen) e só há candidato quando o run normalizado
# tem EXATAMENTE 11 (CPF) ou 15 (CNS) dígitos. Runs que normalizam para outro
# tamanho não geram candidato — nem parcial —, então um CNS de 15 dígitos
# escrito dígito a dígito não vaza como CPF nem uma sequência de 16 vira CNS.
_DIGIT_RUN_PATTERN = re.compile(r"[0-9](?:[0-9 .\-]*[0-9])?")


@dataclass(frozen=True)
class DeterministicExtraction:
    """Resultado imutável da pré-extração determinística (R1).

    Dígitos de CPF/CNS normalizados (sem pontuação/espaços), deduplicados
    preservando a ordem de primeira ocorrência no texto.
    """

    record_number: str | None = None
    patient_name: str | None = None
    birth_date: date | None = None
    cpf_candidates: tuple[str, ...] = ()
    cns_candidates: tuple[str, ...] = ()


def extract_record_number(text: str) -> str | None:
    """Extrai o nº de ocorrência delegando ao padrão do intake (R2).

    Reuso por import de ``apps.intake.pdf_utils.extract_agency_record_number``
    (padrões "Código: XXXXX" e "RELATÓRIO DE OCORRÊNCIAS … XXXXX") — este
    slice não reimplementa nem duplica os padrões.
    """
    return extract_agency_record_number(text)


def extract_patient_name(text: str) -> str | None:
    """Extrai o nome do paciente dos rótulos "Paciente:"/"Nome do Paciente:"/"Nome:".

    (R3) O valor começa no primeiro rótulo de nome encontrado (case tolerante)
    e vai até a quebra do campo seguinte (próxima linha que inicia um rótulo
    canônico) ou o fim do texto. Rótulo ausente ou valor vazio → ``None``.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = _PATIENT_NAME_FIELD_PATTERN.match(line)
        if match is None:
            continue
        value = _capture_field_value(lines, index, match.end())
        if value is not None:
            return value
    return None


def extract_birth_date(text: str) -> date | None:
    """Extrai a data de nascimento dos rótulos "Nascimento:"/"Data de Nascimento:".

    (R4) Data dd/mm/aaaa (ou dd-mm-aaaa) validada como ``date``; datas
    inexistentes (32/13, 29/02 fora de ano bissexto) são rejeitadas com
    ``None`` quando nenhuma ocorrência válida existe.
    """
    for line in text.splitlines():
        field_match = _BIRTH_DATE_FIELD_PATTERN.search(line)
        if field_match is None:
            continue
        date_match = _DATE_PATTERN.search(line[field_match.end() :])
        if date_match is None:
            continue
        day, month, year = (int(group) for group in date_match.groups())
        try:
            return date(year=year, month=month, day=day)
        except ValueError:
            # Data inexistente nesta ocorrência do rótulo — segue para as demais.
            continue
    return None


def extract_cpf_candidates(text: str) -> tuple[str, ...]:
    """Candidatos CPF: runs de dígitos+separadores com exatamente 11 dígitos (R5)."""
    return _digit_runs_with_count(text, 11)


def extract_cns_candidates(text: str) -> tuple[str, ...]:
    """Candidatos CNS: runs de dígitos+separadores com exatamente 15 dígitos (R5)."""
    return _digit_runs_with_count(text, 15)


def run_deterministic_extraction(text: str) -> DeterministicExtraction:
    """Compõe todos os extratores sobre o texto extraído (R6). Função pura."""
    return DeterministicExtraction(
        record_number=extract_record_number(text),
        patient_name=extract_patient_name(text),
        birth_date=extract_birth_date(text),
        cpf_candidates=extract_cpf_candidates(text),
        cns_candidates=extract_cns_candidates(text),
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def _digit_runs_with_count(text: str, count: int) -> tuple[str, ...]:
    """Runs de dígitos+separadores que normalizam para exatamente ``count`` dígitos.

    O run completo é o contrato de boundary: um run cujo total normalizado não
    é ``count`` não produz candidato (nem parcial). Deduplica preservando a
    ordem de primeira ocorrência no texto.
    """
    seen: set[str] = set()
    candidates: list[str] = []
    for match in _DIGIT_RUN_PATTERN.finditer(text):
        digits = re.sub(r"[^0-9]", "", match.group(0))
        if len(digits) != count or digits in seen:
            continue
        seen.add(digits)
        candidates.append(digits)
    return tuple(candidates)


def _capture_field_value(lines: list[str], label_index: int, value_start: int) -> str | None:
    """Valor do campo a partir de ``value_start`` até a quebra do campo seguinte."""
    parts: list[str] = []
    remainder = lines[label_index][value_start:].strip()
    if remainder:
        parts.append(remainder)
    for line in lines[label_index + 1 :]:
        if _starts_field(line):
            break
        if line.strip():
            parts.append(line.strip())
    if not parts:
        return None
    return " ".join(parts)


def _starts_field(line: str) -> bool:
    """A linha inicia um novo campo (rótulo canônico do relatório SESAB)."""
    return _FIELD_BREAK_PATTERN.match(_fold(line)) is not None


def _fold(text: str) -> str:
    """Normaliza para casamento: sem acentos, case-insensitive, espaços colapsados."""
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.lower()
