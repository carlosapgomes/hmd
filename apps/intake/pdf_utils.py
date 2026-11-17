"""Utilitários de extração de texto de PDF (slice 002, design D3).

Funções puras da caixa determinística de extração do relatório SESAB:
``extract_document_text`` (texto via PyMuPDF), ``strip_watermark`` (marca
d'água = token de 5–6 dígitos em linhas de marca) e
``extract_agency_record_number`` (nº de ocorrência dos padrões explícitos).

Divergência marcada vs ats-web: o ats-web resolve extração do nº + remoção da
marca numa função combinada (``strip_watermark_and_extract_record``); o HMD
divide em duas funções com ordem explícita — o nº é extraído do TEXTO BRUTO
ANTES do ``strip_watermark`` (R5b/D3), senão seria perdido quando o nº também
é a marca d'água repetida. O texto armazenado (``Case.extracted_text``) é o já
limpo; o nº vai para ``Case.agency_record_number``.

Marca d'água (watermark-strip-hardening, D1–D4): a premissa de produção é que a
marca QUASE NUNCA coincide com o nº de ocorrência — é um token distinto. Uma
linha de marca é uma linha composta exclusivamente de repetições (1..N) do
mesmo token de 5–6 dígitos (banda cheia, diagonal com poucas repetições ou
linha solta); o token é nomeado com ≥2 linhas de marca no documento
(``_MIN_MARK_LINES``). As linhas de marca dos nomeados são descartadas; o
sweep além dessas linhas é ASSIMÉTRICO — só o token que coincide com o
``record_number`` é varrido globalmente (caso raro); ocorrências inline de
outros tokens nomeados são preservadas (mais provavelmente valor clínico
legítimo do que rastro de marca). ``strip_watermark`` devolve
``WatermarkStripResult`` com ``WatermarkReport`` de contagens (sem valores de
token — base da telemetria do evento de extração).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

import pymupdf

# Linha de marca d'água (D1): linha composta EXCLUSIVAMENTE de repetições
# (1..N) de um mesmo token de 5–6 dígitos — unifica banda cheia (4+), diagonal
# com poucas repetições (2–3) e linha solta (1). Nada mais na linha.
_MARK_LINE_PATTERN = re.compile(r"^\s*(\d{5,6})(?:\s+\1)*\s*$")
# Limiar de nomeação (D1): o token só é nomeado com ≥2 linhas de marca no
# documento. Observação de domínio: mesmo relatórios de página única têm ao
# menos 2 linhas com a marca — marca de linha única não ocorre no corpus; o
# ≥2 guarda contra a linha esporânea isolada de tabela. Valor do corpus, não
# operacional — sem setting novo.
_MIN_MARK_LINES = 2

# Padrões explícitos do nº de ocorrência (R3): rótulo "Código: XXXXX" e o
# cabeçalho "RELATÓRIO DE OCORRÊNCIAS … XXXXX" (case/acentos/formatação
# tolerantes). Espelho dos padrões do ats-web.
_CODE_LABEL_PATTERN = re.compile(
    r"\bC(?:[oO]|[óÓ])digo\s*:\s*([0-9]{5,})\b",
    flags=re.IGNORECASE,
)
_REPORT_HEADER_PATTERN = re.compile(
    r"RELAT(?:[OÓ])RIO\s+DE\s+OCORR(?:[EÊ])NCIAS"
    r"(?:\s*[:\-])?"
    r"[\s\S]{0,120}?"
    r"\b([0-9]{5,})\b",
    flags=re.IGNORECASE,
)


def extract_document_text(path: str) -> str:
    """Abre o PDF com PyMuPDF e concatena o texto de todas as páginas (R1).

    PDF corrompido/ilegível propaga a exceção do PyMuPDF (a task do slice 003
    a converte em ``fail_processing``); PDF válido sem camada de texto → ""
    (o gate retém depois, por ``below_min_chars``). O documento é sempre
    fechado, inclusive em erro de leitura.
    """
    # A superfície de tipos do pymupdf 1.28.2 é parcial: open/close/save são
    # untyped (ignores pontuais); a iteração usa indexação tipada (Page).
    document = pymupdf.open(path)  # type: ignore[no-untyped-call]
    try:
        text = "\n".join(
            document[page_number].get_text()  # type: ignore[no-untyped-call]
            for page_number in range(document.page_count)
        )
    finally:
        document.close()  # type: ignore[no-untyped-call]
    return text.strip()


@dataclass(frozen=True)
class WatermarkReport:
    """Contagens da remoção de marca d'água, sem valores de token (D3).

    Semântica fechada: ``tokens`` = tokens distintos nomeados; ``mark_lines`` =
    linhas de marca descartadas; ``swept_occurrences`` = apenas ocorrências
    inline removidas pelo sweep do token coincidente com o nº (exclui o
    conteúdo das linhas de marca descartadas).
    """

    tokens: int = 0
    mark_lines: int = 0
    swept_occurrences: int = 0


@dataclass(frozen=True)
class WatermarkStripResult:
    """Texto limpo + relatório de contagens da remoção (D3, imutável)."""

    text: str
    report: WatermarkReport


def strip_watermark(text: str, *, record_number: str | None = None) -> WatermarkStripResult:
    """Remove a marca d'água: linhas de marca de token de 5–6 dígitos (R2, D1–D3).

    Nomeação unificada: a linha de marca tem 1..N repetições do token e nada
    mais (banda cheia, diagonal parcial ou linha solta); o token é nomeado com
    ≥2 linhas de marca (``_MIN_MARK_LINES``) e suas linhas de marca são
    descartadas. Sweep assimétrico (D2): só o token nomeado que coincide com
    ``record_number`` é varrido em qualquer ocorrência delimitada do texto
    (caso raro — a marca quase nunca é o nº); token nomeado ≠ nº (ou nº
    ausente) não tem sweep inline — a citação inline é preservada. Devolve
    ``WatermarkStripResult`` com contagens fechadas (D3); sem nomeados, o texto
    permanece byte-idêntico e o report zerado (early-return, R2).
    """
    lines = text.splitlines()

    # 1. Nomeia os tokens com ≥2 linhas de marca no documento.
    mark_line_counts: Counter[str] = Counter()
    for line in lines:
        match = _MARK_LINE_PATTERN.match(line)
        if match:
            mark_line_counts[match.group(1)] += 1

    named_tokens = {token for token, count in mark_line_counts.items() if count >= _MIN_MARK_LINES}
    if not named_tokens:
        return WatermarkStripResult(text, WatermarkReport())

    # 2. Descarta as linhas de marca dos nomeados.
    filtered_lines: list[str] = []
    mark_lines = 0
    for line in lines:
        match = _MARK_LINE_PATTERN.match(line)
        if match and match.group(1) in named_tokens:
            mark_lines += 1
            continue
        filtered_lines.append(line)
    partially_cleaned = "\n".join(filtered_lines)

    # 3. Sweep assimétrico: só o token == nº é varrido inline (D2).
    result = partially_cleaned
    swept_occurrences = 0
    if record_number is not None and record_number in named_tokens:
        result, swept_occurrences = re.subn(rf"\b{re.escape(record_number)}\b", " ", result)

    # 4. Normaliza o whitespace do texto limpo.
    cleaned = _normalize_whitespace(result)
    report = WatermarkReport(
        tokens=len(named_tokens),
        mark_lines=mark_lines,
        swept_occurrences=swept_occurrences,
    )
    return WatermarkStripResult(cleaned, report)


def extract_agency_record_number(text: str) -> str | None:
    """Extrai o nº de ocorrência dos padrões explícitos do relatório (R3).

    Padrões: ``Código: XXXXX`` e ``RELATÓRIO DE OCORRÊNCIAS … XXXXX``
    (case/acentos/formatação tolerantes). Ausente → ``None`` (sem fallback
    inventado — divergência do ats-web, que usava timestamp). Deve rodar sobre
    o TEXTO BRUTO, antes do ``strip_watermark`` (R5b/D3).
    """
    for pattern in (_CODE_LABEL_PATTERN, _REPORT_HEADER_PATTERN):
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


# ── Catálogo canônico de rótulos de campo do cabeçalho SESAB ─────────────────
# Fonte ÚNICA (change painel-ats-parity, slice 001, R1/D1): a lista legada do
# ``apps/anonymization/deterministic.py`` muda de casa e nasce EXPANDIDA com os
# rótulos do cabeçalho que faltavam (``sexo``/``idade``/``raca/cor``/
# ``dias unid.``/``nome social``). Forma canônica: minúsculo, sem acento,
# espaços colapsados (a comparação é sobre linhas passadas por ``_fold_line``);
# o casamento é por PREFIXO da linha seguido de ``:`` ou fim de linha. O
# ``deterministic.py`` importa esta lista para compilar a quebra de campo dos
# valores (mesma direção do reuso de ``extract_agency_record_number`` —
# dependência deterministic→pdf_utils, sem ciclo).
SESAB_FIELD_LABELS: tuple[str, ...] = (
    # Identificação do paciente (rótulos de nome e nascimento — R3/R4).
    "nome do paciente",
    "paciente",
    "nome",
    "nome social",
    "data de nascimento",
    "nascimento",
    "cpf",
    "cns",
    # Linha de demografia e campos administrativos do cabeçalho.
    "sexo",
    "idade",
    "raca/cor",
    "dias unid.",
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


def _fold_line(text: str) -> str:
    """Normaliza uma linha do relatório para casamento de rótulo.

    Espelho exato do ``_fold`` do ``anonymization.deterministic``: sem
    acentos, minúsculo, espaços colapsados.
    """
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.lower()


def _starts_sesab_field(line: str) -> bool:
    """A linha inicia um rótulo de campo do catálogo SESAB (fold).

    Mesma semântica do padrão de quebra do ``deterministic``: rótulo como
    prefixo da linha seguido de ``:`` ou do fim da linha.
    """
    folded = _fold_line(line)
    for label in SESAB_FIELD_LABELS:
        if not folded.startswith(label):
            continue
        rest = folded[len(label) :].lstrip()
        if rest == "" or rest.startswith(":"):
            return True
    return False


# ── Unidade de origem do cabeçalho (change painel-ats-parity, slice 001,
# R1/D1) ──────────────────────────────────────────────────────────────────────
# Layout real do corpus: ``Unid. Origem:`` SOZINHO numa linha e o nome da
# unidade (~7 palavras institucionais, sigla + hífen) na linha SEGUINTE — mesma
# forma multilinha de ``Dias em tela``; a variante ``Unidade de Origem:`` é
# aceita (rótulo canônico do gate). A linha seguinte só vira valor quando é
# plausível: não-vazia E não inicia outro rótulo de campo do catálogo SESAB.
_ORIGIN_UNIT_WITH_VALUE_PATTERN = re.compile(
    r"^\s*(?:Unid\.?\s*Origem|Unidade\s+de\s+Origem)\s*:\s*(?P<value>.*)$",
    flags=re.IGNORECASE,
)
_ORIGIN_UNIT_ALONE_PATTERN = re.compile(
    r"^\s*(?:Unid\.?\s*Origem|Unidade\s+de\s+Origem)\s*:?\s*$",
    flags=re.IGNORECASE,
)
# Defesa de tamanho do campo (``Case.origin_unit`` é CharField(128)).
_ORIGIN_UNIT_MAX_LENGTH = 128

# ── Cabeçalho padrão SESAB: metadados do caso (change sesab-header-extraction,
# slice 001, R1/D3) ───────────────────────────────────────────────────────────
# O cabeçalho repete-se por página e, na extração linear do PyMuPDF, rótulos e
# valores desalinham: a demografia vem na linha ANTES do rótulo ``Paciente:``
# sozinho (``<NOME> - Idade: 79a. - Sexo: F - Raça/Cor: Parda``). A âncora
# anti-falso-positivo exige os TRÊS marcadores NA MESMA LINHA, nesta ordem —
# idade mencionada em texto clínico isolado não casa. Fonte única do pattern:
# o slice 002 da anonymization importa daqui (mesma direção do reuso de
# ``extract_agency_record_number``).
# Enum IBGE do DATASUS (feminino, como no formulário do SUS) + ausência —
# domínio FECHADO: aberturas de sufixo (``[ao]``, ``Fem?``) ampliariam o
# domínio além do contrato e casariam variantes não-IBGE (P1 da review do
# slice 002); acentuação/case seguem tolerantes (extração de PDF).
_RACE_ALTERNATION = r"Branca|Preta|Parda|Amarela|Ind[íi]gena|N[ãa]o informado"
# Sexo do cabeçalho: letra ("Sexo: F") ou palavra completa ("Sexo Feminino"/
# "Sexo Masculino", layout real do corpus) — dois-pontos OPCIONAL; abreviações
# ("Fem"/"Masc") estão FORA do domínio.
_GENDER_ALTERNATION = r"F|M|Feminino|Masculino"
DEMOGRAPHICS_LINE_PATTERN = re.compile(
    r"Idade\s*:\s*(?P<age>\d+)\s*a\.?"
    rf".*?Sexo\s*:?\s*(?P<gender>{_GENDER_ALTERNATION})\b"
    rf".*?Ra[çc]a\s*/\s*Cor\s*:?\s*(?P<race>{_RACE_ALTERNATION})\b",
    flags=re.IGNORECASE,
)

# ``Dias em tela``: forma mesma-linha (``Dias em tela: 3``) e forma do layout
# real (rótulo sozinho com o valor na linha imediatamente seguinte, contendo
# APENAS o inteiro).
_DAYS_ON_SCREEN_LABEL_PATTERN = re.compile(r"^Dias\s+em\s+tela\s*:\s*$", flags=re.IGNORECASE)
_DAYS_ON_SCREEN_INLINE_PATTERN = re.compile(r"Dias\s+em\s+tela\s*:\s*(\d+)", flags=re.IGNORECASE)
_INTEGER_ONLY_LINE_PATTERN = re.compile(r"^\s*\d+\s*$")


@dataclass(frozen=True)
class HeaderMetadata:
    """Metadados do cabeçalho padrão SESAB do relatório (R1, imutável).

    Campos ausentes no texto ficam ``None`` (relatório sem cabeçalho não é
    erro — D3).
    """

    age: int | None = None
    gender: str | None = None
    race: str | None = None
    days_on_screen: int | None = None
    origin_unit: str | None = None


def extract_header_metadata(text: str) -> HeaderMetadata:
    """Extrai idade/sexo/raça/dias-em-tela do cabeçalho padrão SESAB (R1/D3).

    Idade/sexo/raça vêm SOMENTE da linha de demografia canônica
    (``DEMOGRAPHICS_LINE_PATTERN``: os três marcadores na mesma linha, na
    ordem) — menção clínica isolada de idade não gera metadado.
    ``days_on_screen`` é o MAIOR valor de ``Dias em tela`` entre as
    ocorrências do texto (molde ats-web), aceitando a forma mesma-linha e a
    forma multilinha do layout real. ``origin_unit`` vem do rótulo
    ``Unid. Origem:`` (mesma-linha ou rótulo sozinho + valor plausível na
    linha seguinte). Gênero normalizado para a letra (F/M): o
    layout real traz a palavra completa (``Sexo Feminino``) e outros trazem a
    letra (``Sexo: F``). Função pura, zero I/O.
    """
    age: int | None = None
    gender: str | None = None
    race: str | None = None
    for line in text.splitlines():
        match = DEMOGRAPHICS_LINE_PATTERN.search(line)
        if match is not None:
            age = int(match.group("age"))
            # Gênero normalizado para a letra (F/M): o layout real traz a
            # palavra completa ("Feminino") e outros trazem a letra.
            gender = match.group("gender").strip().upper()[0]
            race = match.group("race")
            break
    return HeaderMetadata(
        age=age,
        gender=gender,
        race=race,
        days_on_screen=_extract_days_on_screen(text),
        origin_unit=_extract_origin_unit(text),
    )


def _extract_origin_unit(text: str) -> str | None:
    """Unidade de origem do cabeçalho padrão SESAB (R1/D1).

    Duas formas do layout real: valor na MESMA linha do rótulo
    (``Unid. Origem: <unidade>``) ou rótulo sozinho com o valor na linha
    imediatamente seguinte. A linha seguinte só é aceita quando é plausível —
    não-vazia E não inicia outro rótulo de campo do catálogo canônico
    (``_starts_sesab_field``); rótulo sozinho no fim do texto ou seguido de
    linha vazia/de rótulo → sem valor (nada inventado). Primeira ocorrência
    vence; o valor é truncado em ``_ORIGIN_UNIT_MAX_LENGTH`` (defesa do
    campo).
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        with_value = _ORIGIN_UNIT_WITH_VALUE_PATTERN.match(line)
        if with_value is not None:
            value = with_value.group("value").strip()
            if value:
                return value[:_ORIGIN_UNIT_MAX_LENGTH]
            # Rótulo com dois-pontos e sem valor: mesma semântica do rótulo
            # sozinho — o valor pode estar na linha imediatamente seguinte.
        elif _ORIGIN_UNIT_ALONE_PATTERN.match(line) is None:
            continue
        if index + 1 >= len(lines):
            continue
        candidate = lines[index + 1].strip()
        if candidate and not _starts_sesab_field(candidate):
            return candidate[:_ORIGIN_UNIT_MAX_LENGTH]
    return None


def _extract_days_on_screen(text: str) -> int | None:
    """Maior ``Dias em tela`` do texto (mesma-linha ou rótulo+inteiro abaixo)."""
    lines = text.splitlines()
    values: list[int] = []
    for index, line in enumerate(lines):
        # Todas as ocorrências inline da linha contam (contrato: maior valor
        # entre TODAS as ocorrências — P2 da review do slice 001).
        for inline in _DAYS_ON_SCREEN_INLINE_PATTERN.finditer(line):
            values.append(int(inline.group(1)))
        if _DAYS_ON_SCREEN_INLINE_PATTERN.search(line) is not None:
            continue
        if (
            _DAYS_ON_SCREEN_LABEL_PATTERN.match(line.strip())
            and index + 1 < len(lines)
            and _INTEGER_ONLY_LINE_PATTERN.match(lines[index + 1])
        ):
            values.append(int(lines[index + 1].strip()))
    return max(values) if values else None


def _normalize_whitespace(text: str) -> str:
    """Normaliza espaços preservando as quebras de linha entre parágrafos."""
    normalized_lines: list[str] = []
    for raw_line in text.splitlines():
        compact = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not compact:
            if normalized_lines and normalized_lines[-1] != "":
                normalized_lines.append("")
            continue
        normalized_lines.append(compact)
    return "\n".join(normalized_lines).strip()
