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

O nome do paciente vem do cabeçalho padrão SESAB (change
sesab-header-extraction, slice 002): a âncora da linha de DEMOGRAFIA
(``<NOME> - Idade: N a. - Sexo: … - Raça/Cor: …`` com o rótulo ``Paciente:``
sozinho na linha seguinte — ``DEMOGRAPHICS_LINE_PATTERN`` importado do
intake) ANTES dos rótulos mesma-linha, que permanecem para outros layouts. O
nome social (``social_name``) é captura conservadora: só valor à direita do
rótulo, na MESMA linha — candidato PESSOA próprio, NUNCA linkage.

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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from apps.intake.pdf_utils import (
    DEMOGRAPHICS_LINE_PATTERN,
    SESAB_FIELD_LABELS,
    extract_agency_record_number,
)

# ── Rótulos canônicos de campo do relatório SESAB (quebra de valor) ───────
# O catálogo vive em ``apps.intake.pdf_utils.SESAB_FIELD_LABELS`` (fonte
# única — change painel-ats-parity, slice 001, R1/D1; a lista nasceu lá
# EXPANDIDA com os rótulos do cabeçalho que faltavam); a importação segue a
# direção vigente deterministic→pdf_utils (sem ciclo), como
# ``extract_agency_record_number``. Minúsculos/sem acento (a comparação é
# sobre linhas normalizadas por fold); o casamento é por prefixo da linha
# seguido de ``:`` ou fim de linha.
_FIELD_BREAK_PATTERN = re.compile(
    r"^(?:" + "|".join(re.escape(label) for label in SESAB_FIELD_LABELS) + r")(?:\s*:|\s*$)"
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

# ── Cabeçalho padrão SESAB (change sesab-header-extraction, slice 002, R1/D1) ─
# Na extração linear do PyMuPDF o nome COMPLETO do paciente vem na linha de
# DEMOGRAFIA — ``<NOME> - Idade: 79a. - Sexo Feminino - Raça/Cor PARDA``, já
# no layout real do corpus (gênero por extenso e dois-pontos opcional) — e o
# rótulo ``Paciente:`` aparece SOZINHO na linha SEGUINTE, bloco repetido a cada
# página. O padrão da linha de demografia é a fonte única importada de
# ``apps.intake.pdf_utils`` (mesma direção do reuso de
# ``extract_agency_record_number``): os três marcadores NA MESMA linha impedem
# que menção clínica isolada de idade vire nome (âncora tripla).
_PACIENTE_LABEL_ALONE_PATTERN = re.compile(r"^paciente\s*:?\s*$")
_NAME_BEFORE_AGE_PATTERN = re.compile(r"^(?P<name>.+?)\s*-?\s*Idade\s*:", flags=re.IGNORECASE)

# Nome social (R2/D2): captura CONSERVADORA — apenas valor à direita do rótulo,
# na MESMA linha; rótulo sozinho (campo vazio) não faz fallback para linhas
# vizinhas (texto clínico não-nominal seria capturado).
_SOCIAL_NAME_FIELD_PATTERN = re.compile(
    r"^\s*Nome\s+Social\s*:\s*(?P<value>\S.*?)\s*$",
    flags=re.IGNORECASE,
)

# Candidatos CPF/CNS (11/15 dígitos, sem checksum neste slice): em vez de
# lookarounds sobre dígitos adjacentes, varre-se o RUN completo de dígitos +
# separadores (espaço/ponto/hífen) e só há candidato quando o run normalizado
# tem EXATAMENTE 11 (CPF) ou 15 (CNS) dígitos. Runs que normalizam para outro
# tamanho não geram candidato — nem parcial —, então um CNS de 15 dígitos
# escrito dígito a dígito não vaza como CPF nem uma sequência de 16 vira CNS.
_DIGIT_RUN_PATTERN = re.compile(r"[0-9](?:[0-9 .\-]*[0-9])?")

# CNS estrutural do paciente (slice 001 da change reapresentacao-casos-filas,
# R1/D1 revision002): gramatica textual FECHADA — somente o slot imediato
# ``Central Estadual de Regulacao`` -> ``CNS:`` -> ``Dias em tela:`` dentro do
# candidato titulo -> Nome Social. Linha ``CNS:`` com whitespace horizontal
# opcional antes/depois dos dois-pontos; valor na MESMA linha (nunca a
# seguinte). D1 normalizado por acentos/caixa/whitespace; sem janelas de
# distancia, numeros de linha fixos ou salto sobre outros conteudos.
_PATIENT_CNS_LINE_PATTERN = re.compile(
    r"^\s*CNS[ \t]*:[ \t]*(?P<value>.*?)\s*$", flags=re.IGNORECASE
)
_DIAS_EM_TELA_LINE_PATTERN = re.compile(r"^\s*Dias\s+em\s+tela\s*:", flags=re.IGNORECASE)
_TITLE_FOLDED = "relatorio de ocorrencias"
_SIGNATURE_FOLDED = "central estadual de regulacao"

# Seccoes clinicas/narrativas (limites de rejeicao do candidato D1): se
# aparecem entre o titulo e o Nome Social, o candidato e ignorado — nunca
# se procura outro Nome Social alem da narrativa. Catalogo ja existente.
_NARRATIVE_SECTION_LABELS: frozenset[str] = frozenset(
    {"resumo clinico", "motivo da solicitacao", "complemento da solicitacao"}
)
# Gramatica do valor (fix-1 P1-01, mantida): SOMENTE digitos e separadores
# usuais (espaco/tab/ponto/hifen) — letras ou outros simbolos colados aos
# digitos invalidam a identidade em vez de serem silenciosamente removidos.
_CNS_VALUE_ALLOWED_PATTERN = re.compile(r"[0-9 \t.\-]*")


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
    # Nome social do cabeçalho, quando preenchido e válido (R2/D2): candidato
    # PESSOA PRÓPRIO na anonimização — nunca alimenta o linkage do caso.
    social_name: str | None = None
    # CNS estrutural do paciente (reapresentacao-casos-filas, slice 001,
    # R1/D1): somente dígitos validados do rótulo ``CNS:`` do cabeçalho;
    # ausente/inválido/ambíguo → ``None`` (identidade vazia, sem inferência).
    patient_cns: str | None = None


def extract_record_number(text: str) -> str | None:
    """Extrai o nº de ocorrência delegando ao padrão do intake (R2).

    Reuso por import de ``apps.intake.pdf_utils.extract_agency_record_number``
    (padrões "Código: XXXXX" e "RELATÓRIO DE OCORRÊNCIAS … XXXXX") — este
    slice não reimplementa nem duplica os padrões.
    """
    return extract_agency_record_number(text)


def extract_patient_name(text: str) -> str | None:
    """Extrai o nome do paciente do cabeçalho do relatório SESAB (R3).

    Ordem de tentativa (change sesab-header-extraction, slice 002, R1/D1):

    1. **Âncora do cabeçalho padrão**: linha de demografia canônica
       (``DEMOGRAPHICS_LINE_PATTERN`` — ``Idade``+``Sexo``+``Raça/Cor`` na
       MESMA linha, na ordem) cuja linha seguinte é o rótulo ``Paciente:``
       sozinho; nome = prefixo da linha até ``Idade:``, validado com ≥ 2
       palavras com letras;
    2. rótulos mesma-linha ``Paciente:``/``Nome do Paciente:``/``Nome:``
       (case tolerante) — valor até a quebra do campo seguinte —, que
       permanecem para outros layouts.

    Rótulo ausente ou valor vazio → ``None``.
    """
    lines = text.splitlines()
    anchored = _patient_name_from_header_anchor(lines)
    if anchored is not None:
        return anchored
    for index, line in enumerate(lines):
        match = _PATIENT_NAME_FIELD_PATTERN.match(line)
        if match is None:
            continue
        value = _capture_field_value(lines, index, match.end())
        if value is not None:
            return value
    return None


def extract_social_name(text: str, patient_name: str | None = None) -> str | None:
    """Extrai o nome social do rótulo ``Nome Social:`` com valor na MESMA linha (R2/D2).

    Captura conservadora: SÓ o valor à direita do rótulo — rótulo sozinho (campo
    vazio, caso do relatório SESAB real) não faz fallback para linhas vizinhas.
    O valor precisa ser nominal (apenas letras/espaços, com ao menos uma palavra
    de 2+ letras) e DIFERENTE do nome civil (fold). O nome social não alimenta o
    linkage do caso: é apenas candidato PESSOA próprio na anonimização.
    """
    for line in text.splitlines():
        match = _SOCIAL_NAME_FIELD_PATTERN.match(line)
        if match is None:
            continue
        value = match.group("value").strip()
        if not _is_nominal_value(value):
            continue
        if patient_name is not None and _fold(value) == _fold(patient_name):
            continue
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


def extract_patient_cns(text: str) -> str | None:
    """CNS estrutural do paciente no slot do bloco textual D1 (R1/revision002).

    Gramatica FECHADA (substitui proximidade/reinicio por demografia):
    somente a linha de titulo inteiro ``RELATORIO DE OCORRENCIAS``
    (normalizada por acentos/caixa/whitespace) abre candidato; comeco do
    documento, demografia ou rotulos isolados NUNCA abrem/reabrem. O candidato
    termina no PRIMEIRO ``Nome Social:``, proximo titulo, inicio de secao
    clinica ou EOF — soh eh valido se o termino for Nome Social (valor na
    mesma linha ou rotulo sozinho). Dentro dele, exigir rotulos distintos
    ``Paciente:`` e ``Codigo:`` ANTES da assinatura ``Central Estadual de
    Regulacao`` (linha inteira normalizada); depois dela, a proxima linha
    nao vazia deve ser ``CNS:`` (valor opcional na MESMA linha) e a seguinte
    ``Dias em tela:`` — sem conteudo interveniente, sem buscar valor na
    linha seguinte, sem janela de distancia. CNS fora desse slot nao
    participa; mais de um slot no mesmo candidato = ambiguidade.

    A pertinencia do bloco/slot eh validada ANTES de formato/checksum/taint:
    narrativa (mesmo invalida) nunca contamina; somente valor nao vazio
    invalido em slot estrutural valido invalida a identidade. Blocos validos
    repetidos deduplicam o mesmo CNS; valores validos distintos deixam a
    identidade vazia. Bloco sem estrutura reconhecida eh ignorado; sem bloco
    elegivel, retorna ``None`` sem bloquear o caso.
    """
    # Import tardio: o validador vive em ``recognizers`` (que importa o
    # Presidio); o reuso aqui nao cria dependencia de import do modulo puro.
    from apps.anonymization.recognizers import valid_cns

    lines = text.splitlines()
    title_indexes = [i for i, line in enumerate(lines) if _is_title_line(line)]
    if not title_indexes:
        return None
    seen: set[str] = set()
    found_structural = False
    for order, title_idx in enumerate(title_indexes):
        endpoint_idx, endpoint_kind = _candidate_endpoint(lines, title_idx)
        if endpoint_kind != "nome_social":
            # Falta titulo/assinatura/termino, secao clinica ou proximo titulo
            # antes do Nome Social: formato parcial, candidato ignorado.
            continue
        slots = _structural_slots(lines, title_idx, endpoint_idx)
        if not slots:
            continue
        if len(slots) > 1:
            # Mais de um slot estrutural no mesmo candidato: ambiguidade.
            return None
        signature_idx, cns_idx, _dias_idx = slots[0]
        if not _has_patient_code_before(lines, title_idx, signature_idx):
            continue
        found_structural = True
        match = _PATIENT_CNS_LINE_PATTERN.match(lines[cns_idx])
        # ``_structural_slots`` soh coleta linhas que casam o padrao.
        assert match is not None
        raw_value = match.group("value").strip()
        if not raw_value:
            # Rotulo vazio legitimo: nao busca a linha seguinte.
            continue
        if _CNS_VALUE_ALLOWED_PATTERN.fullmatch(raw_value) is None:
            return None
        digits = re.sub(r"[^0-9]", "", raw_value)
        if not digits or not valid_cns(digits):
            return None
        seen.add(digits)
        _ = order
    if not found_structural or len(seen) != 1:
        return None
    return next(iter(seen))


def run_deterministic_extraction(text: str) -> DeterministicExtraction:
    """Compõe todos os extratores sobre o texto extraído (R6). Função pura."""
    patient_name = extract_patient_name(text)
    return DeterministicExtraction(
        record_number=extract_record_number(text),
        patient_name=patient_name,
        birth_date=extract_birth_date(text),
        cpf_candidates=extract_cpf_candidates(text),
        cns_candidates=extract_cns_candidates(text),
        social_name=extract_social_name(text, patient_name=patient_name),
        patient_cns=extract_patient_cns(text),
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def _patient_name_from_header_anchor(lines: Sequence[str]) -> str | None:
    """Nome do paciente pela âncora da linha de demografia do cabeçalho (R1).

    Exige as TRÊS condições: linha de demografia canônica (marcadores
    ``Idade``+``Sexo``+``Raça/Cor`` na mesma linha), rótulo ``Paciente:`` sozinho
    na linha SEGUINTE e prefixo com ≥ 2 palavras com letras. Primeira ocorrência
    válida vence (blocos repetidos por página).
    """
    for index, line in enumerate(lines[:-1]):
        if DEMOGRAPHICS_LINE_PATTERN.search(line) is None:
            continue
        if _PACIENTE_LABEL_ALONE_PATTERN.match(_fold(lines[index + 1])) is None:
            continue
        match = _NAME_BEFORE_AGE_PATTERN.match(line)
        if match is None:
            continue
        name = match.group("name").strip().strip("-–—").strip()
        if _has_at_least_two_letter_words(name):
            return name
    return None


def _has_at_least_two_letter_words(value: str) -> bool:
    """O valor tem ao menos duas palavras contendo letras (nome completo)."""
    words = [word for word in value.split() if any(char.isalpha() for char in word)]
    return len(words) >= 2


def _is_nominal_value(value: str) -> bool:
    """O valor é nominal: só letras/espaços, com ao menos uma palavra de 2+ letras."""
    words = value.split()
    if not words:
        return False
    if not all(word.isalpha() for word in words):
        return False
    return any(len(word) >= 2 for word in words)


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


def _sesab_field_label(line: str) -> str | None:
    """Rótulo canônico do campo SESAB que inicia a linha, se houver (fix-1 P1-01)."""
    folded = _fold(line)
    for label in SESAB_FIELD_LABELS:
        if not folded.startswith(label):
            continue
        rest = folded[len(label) :].lstrip()
        if rest == "" or rest.startswith(":"):
            return label
    return None


def _is_title_line(line: str) -> bool:
    """Linha de titulo inteiro D1 (normalizada por acentos/caixa/whitespace)."""
    return _fold(line) == _TITLE_FOLDED


def _is_nome_social_line(line: str) -> bool:
    """Rotulo ``Nome Social:`` sozinho ou com valor na mesma linha."""
    return _sesab_field_label(line) == "nome social"


def _is_clinical_section_line(line: str) -> bool:
    """Inicio de secao clinica: limite de rejeicao, nunca reabertura."""
    return _sesab_field_label(line) in _NARRATIVE_SECTION_LABELS


def _is_signature_line(line: str) -> bool:
    """Assinatura institucional D1: linha inteira normalizada."""
    return _fold(line) == _SIGNATURE_FOLDED


def _is_dias_line(line: str) -> bool:
    """Rotulo ``Dias em tela:`` com ou sem valor, case-insensitive."""
    return _DIAS_EM_TELA_LINE_PATTERN.match(line) is not None


def _candidate_endpoint(lines: Sequence[str], title_idx: int) -> tuple[int, str]:
    """Primeiro terminador apos o titulo: nome_social/title/clinical/eof."""
    for idx in range(title_idx + 1, len(lines)):
        line = lines[idx]
        if _is_nome_social_line(line):
            return idx, "nome_social"
        if _is_title_line(line):
            return idx, "title"
        if _is_clinical_section_line(line):
            return idx, "clinical"
    return len(lines), "eof"


def _next_nonblank(lines: Sequence[str], start: int, end: int) -> int | None:
    """Proxima linha nao vazia em ``[start, end)`` ou ``None``."""
    for idx in range(start, end):
        if lines[idx].strip():
            return idx
    return None


def _structural_slots(
    lines: Sequence[str], title_idx: int, endpoint_idx: int
) -> list[tuple[int, int, int]]:
    """Slots imediatos assinatura -> CNS -> Dias dentro do candidato."""
    slots: list[tuple[int, int, int]] = []
    for sig_idx in range(title_idx + 1, endpoint_idx):
        if not _is_signature_line(lines[sig_idx]):
            continue
        cns_idx = _next_nonblank(lines, sig_idx + 1, endpoint_idx)
        if cns_idx is None:
            continue
        if _PATIENT_CNS_LINE_PATTERN.match(lines[cns_idx]) is None:
            continue
        dias_idx = _next_nonblank(lines, cns_idx + 1, endpoint_idx)
        if dias_idx is None or not _is_dias_line(lines[dias_idx]):
            continue
        slots.append((sig_idx, cns_idx, dias_idx))
    return slots


def _has_patient_code_before(lines: Sequence[str], title_idx: int, signature_idx: int) -> bool:
    """Rotulos distintos ``Paciente:`` e ``Codigo:`` antes da assinatura."""
    has_patient = False
    has_code = False
    for idx in range(title_idx + 1, signature_idx):
        label = _sesab_field_label(lines[idx])
        if label == "paciente":
            has_patient = True
        elif label == "codigo":
            has_code = True
        if has_patient and has_code:
            return True
    return False


def _fold(text: str) -> str:
    """Normaliza para casamento: sem acentos, case-insensitive, espaços colapsados."""
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.lower()
