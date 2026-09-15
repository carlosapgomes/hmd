"""Utilitários de extração de texto de PDF (slice 002, design D3).

Funções puras da caixa determinística de extração do relatório SESAB:
``extract_document_text`` (texto via PyMuPDF), ``strip_watermark`` (marca
d'água = sequência de 5–6 dígitos do registro repetida) e
``extract_agency_record_number`` (nº de ocorrência dos padrões explícitos).

Divergência marcada vs ats-web: o ats-web resolve extração do nº + remoção da
marca numa função combinada (``strip_watermark_and_extract_record``); o HMD
divide em duas funções com ordem explícita — o nº é extraído do TEXTO BRUTO
ANTES do ``strip_watermark`` (R5b/D3), senão seria perdido quando o nº também
é a marca d'água repetida. O texto armazenado (``Case.extracted_text``) é o já
limpo; o nº vai para ``Case.agency_record_number``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import pymupdf

# Linha de marca d'água: o mesmo token de 5–6 dígitos repetido 4+ vezes na
# linha (banda típica de watermark). Espelho da detecção do ats-web.
_REPEATED_FIVE_DIGIT_LINE_PATTERN = re.compile(r"^\s*(\d{5,6})(?:\s+\1){3,}\s*$")
# Token residual de 5–6 dígitos solto no texto (rastro da marca d'água).
_DIGIT_TOKEN_PATTERN = re.compile(r"\b(\d{5,6})\b")

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
        text = "".join(
            document[page_number].get_text()  # type: ignore[no-untyped-call]
            for page_number in range(document.page_count)
        )
    finally:
        document.close()  # type: ignore[no-untyped-call]
    return text.strip()


def strip_watermark(text: str) -> str:
    """Remove a marca d'água: sequência de 5–6 dígitos repetida (R2).

    Estratégia espelhada do ats-web: detecta os tokens que aparecem como linha
    repetida (banda de marca d'água), descarta as bandas e as ocorrências
    isoladas residuais do mesmo token e normaliza o whitespace do resultado.
    Texto sem marca d'água permanece idêntico (retorno antecipado — "texto sem
    marca permanece idêntico", R2).
    """
    lines = text.splitlines()

    # 1. Detecta os tokens de 5–6 dígitos que formam bandas repetidas.
    repeated_token_counts: Counter[str] = Counter()
    for line in lines:
        match = _REPEATED_FIVE_DIGIT_LINE_PATTERN.match(line)
        if match:
            repeated_token_counts[match.group(1)] += 1

    candidate_tokens = {token for token, count in repeated_token_counts.items() if count >= 1}
    if not candidate_tokens:
        return text

    # 2. Remove as linhas de marca d'água (bandas do token candidato).
    filtered_lines: list[str] = []
    for line in lines:
        match = _REPEATED_FIVE_DIGIT_LINE_PATTERN.match(line)
        if match and match.group(1) in candidate_tokens:
            continue
        filtered_lines.append(line)
    partially_cleaned = "\n".join(filtered_lines)

    # 3. Remove os tokens residuais isolados do mesmo registro (rastros).
    token_counts = Counter(_DIGIT_TOKEN_PATTERN.findall(partially_cleaned))
    removable_tokens = {token for token in candidate_tokens if token_counts.get(token, 0) >= 1}
    result = partially_cleaned
    for token in removable_tokens:
        result = re.sub(rf"\b{re.escape(token)}\b", " ", result)

    # 4. Normaliza o whitespace do texto limpo.
    return _normalize_whitespace(result)


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


def extract_header_metadata(text: str) -> HeaderMetadata:
    """Extrai idade/sexo/raça/dias-em-tela do cabeçalho padrão SESAB (R1/D3).

    Idade/sexo/raça vêm SOMENTE da linha de demografia canônica
    (``DEMOGRAPHICS_LINE_PATTERN``: os três marcadores na mesma linha, na
    ordem) — menção clínica isolada de idade não gera metadado.
    ``days_on_screen`` é o MAIOR valor de ``Dias em tela`` entre as
    ocorrências do texto (molde ats-web), aceitando a forma mesma-linha e a
    forma multilinha do layout real. Gênero normalizado para a letra (F/M): o
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
    )


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
