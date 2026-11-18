# Slice 001 — Nomeação unificada por linha de marca + sweep assimétrico + separador de página

## Contexto necessário

- `strip_watermark` (`apps/intake/pdf_utils.py:68`): 4 passos hoje — (1)
  `_REPEATED_FIVE_DIGIT_LINE_PATTERN = r"^\s*(\d{5,6})(?:\s+\1){3,}\s*$"`
  conta bandas por token (`count >= 1` nomeia); (2) remove linhas de banda;
  (3) sweep global `\b{token}\b` de TODOS os candidatos
  (`_DIGIT_TOKEN_PATTERN = r"\b(\d{5,6})\b"`; o filtro `>= 1` do
  `removable_tokens` é redundante); (4) `_normalize_whitespace`
  (`pdf_utils.py:349`, linha a linha, preserva quebras).
- Early-return sem candidatos (texto byte-idêntico) — R2, testado em
  `test_text_without_watermark_intact` (`apps/intake/tests/test_pdf_utils.py`).
- **Premissa corrigida**: a marca quase nunca é o nº de ocorrência
  (observação de produção), e mesmo relatórios de página única têm ao menos
  2 linhas com a marca — o limiar `_MIN_MARK_LINES = 2` não perde marca real.
  Fixtures de banda única do próprio nº codificam o caso raro: `test_pdf_utils.py:100-117,159-180` e `test_tasks.py:37-47,131-158`
  (`_WATERMARK_BAND`/`_RECORD_NUMBER` ambos "33345", asserção
  `_RECORD_NUMBER not in extracted_text`).
- `extract_document_text` (`pdf_utils.py:47`, join em `:59`): `"".join`.
  **Verificado empiricamente**: `Page.get_text()` do PyMuPDF SEMPRE termina
  linha (inclusive a última da página) com `\n` — PDF real não reproduz a
  fusão; a regressão da fronteira exige dublê no seam do `pymupdf.open`.
- Ordem D3/R5b no caller (`apps/intake/tasks.py:228-229`): nº extraído do
  bruto ANTES do strip; hoje `strip_watermark(raw_text)` NÃO recebe o nº.
- Caller produtivo único: a task (`tasks.py:229`). Consumidor adicional de
  `extract_document_text`: `apps/attachments/extraction.py:31,78,112`
  (limiar local×OCR `len(text) > 30` — `\n` extra só soma chars).
- Boundary: tamanho permanece 5–6 dígitos; NENHUMA guard de contexto de
  data/separador (ano tem 4 dígitos, não colide com `\d{5,6}`).

## Goal

Marca em linha de marca (1..N repetições — banda cheia, diagonal parcial,
linha solta) é removida com ≥2 linhas; linha única não nomeia; ocorrência
inline de token ≠ nº preservada; token == nº (raro) varrido globalmente
quando nomeado; páginas separadas por `\n` com contrato explícito; função
devolve relatório de contagens com semântica fechada (base do slice 002);
fixtures migram para a forma observada (marca ≠ nº).

## Deliverables

### R1 — Nomeação unificada por linha de marca (`pdf_utils.py`)

- Padrão novo `_MARK_LINE_PATTERN = r"^\s*(\d{5,6})(?:\s+\1)*\s*$"` (1..N
  repetições do token na linha, nada mais) SUBSTITUI o padrão de banda cheia
  na nomeação e na remoção de linhas.
- Constante módulo `_MIN_MARK_LINES = 2` (design D1 — sem setting novo):
  token nomeado com ≥2 linhas de marca no documento (qualquer mistura de
  formas: banda cheia, parcial, linha solta).
- Linhas de marca dos nomeados são descartadas.

### R2 — Sweep assimétrico + relatório (design D2/D3)

- Assinatura: `strip_watermark(text, *, record_number: str | None = None) ->
  WatermarkStripResult` (dataclasses frozen: `text: str`, `report:
  WatermarkReport`).
- Semântica FECHADA das contagens: `tokens` = distintos nomeados;
  `mark_lines` = linhas de marca descartadas; `swept_occurrences` = apenas
  ocorrências inline removidas pelo sweep (exclui conteúdo das linhas
  descartadas).
- Token nomeado == `record_number` → sweep `\b{token}\b` global, contado em
  `swept_occurrences`; token nomeado ≠ `record_number` (ou nº ausente) →
  SEM sweep inline.
- Remover o filtro redundante `>= 1` do passo 3; sem nomeados →
  `WatermarkStripResult(text, report zerado)`, texto idêntico
  (early-return preservado). `_normalize_whitespace` inalterado (só quando
  houve remoção).

### R3 — Separador de página + docstrings (`pdf_utils.py`)

- `"".join(...)` → `"\n".join(...)`; `text.strip()` final permanece.
- Docstring do MÓDULO `pdf_utils.py` (topo) e da `strip_watermark`: registrar
  a linha de marca unificada, o limiar ≥2, o sweep assimétrico, o relatório
  e a premissa corrigida (marca ≠ nº é o comum) — boundary deste slice (o
  slice 002 não toca este arquivo).

### R4 — Testes + migração de fixtures (`test_pdf_utils.py`, `test_tasks.py`)

RED esperado por teste novo (falham antes da implementação):

- `test_single_mark_line_is_kept`: UMA linha "48291 48291 48291 48291"
  (sem `record_number`) → PERMANECE (hoje remove — RED).
- `test_two_full_band_lines_removed`: 2× banda cheia de "48291" → removidas
  (hoje remove — GREEN por comportamento, RED pelas asserções do report).
- `test_standalone_and_partial_lines_removed`: "48291" solto + "48291
  48291" parcial (2 linhas de marca) → removidas (hoje PERMANECEM — RED).
- `test_inline_occurrence_of_non_record_token_preserved`: 2 linhas de marca
  de "48291" + linha clínica citando "48291" no meio de conteúdo → marca
  sai, inline fica (hoje inline é varrido — RED).
- `test_record_coincident_token_swept_globally`: 2 linhas de marca de
  "33345" + `record_number="33345"` + citação inline → linhas e inline
  varridos (hoje varre com 1 linha — asserções do report definem o RED).
- `test_report_counts_mixed`: token em 3 linhas de marca mistas →
  `tokens`=1, `mark_lines`=3, `swept_occurrences`=0 (RED pela assinatura).
- `test_page_join_separates_lines_without_trailing_newline`: monkeypatch de
  `pymupdf.open` com documento falso (páginas cujo `get_text()` NÃO termina
  em `\n`; página 2 = linha de marca) → removida (hoje funde e o token
  sobrevive — RED; PDF real não reproduz, ver Contexto).

Migração canônica (fixture → forma observada):

- `test_pdf_utils.py`: `test_watermark_stripped`,
  `test_watermark_removed_from_extracted_pdf_text` e família `:159-180` (nº
  do bruto) — dobrar a linha de marca OU introduzir token distinto do nº
  conforme o que cada teste pinna (comentar a premissa corrigida na
  fixture).
- `test_tasks.py`: `_WATERMARK_BAND`/`_RECORD_NUMBER` separam-se — marca
  vira token distinto (ex. "48291" em 2 linhas de marca); `Código: 33345`
  permanece; asserções: `agency_record_number == "33345"`, token de marca
  ausente de `extracted_text`, linha `Código:` presente (o nº inline segue
  no texto — tokenizado depois pela anonimização determinística).

Task (transição mínima no mesmo slice): `result = strip_watermark(raw_text,
record_number=record_number)` / `cleaned_text = result.text`.

- Matriz requisito → arquivo → teste:

| Requisito/spec | Código | Teste |
|---|---|---|
| linha de marca 1..N repetições | pdf_utils.py | banda/solta/parcial |
| ≥2 linhas nomeiam / 1 preserva | pdf_utils.py | 1 vs 2 linhas |
| inline ≠ nº preservado | pdf_utils.py | marca + citação clínica |
| nº coincidente varrido global | pdf_utils.py | coincidente + inline |
| contagens (D3) | pdf_utils.py | report misto |
| fronteira de página (seam) | pdf_utils.py | dublê pymupdf.open |
| fixture da task = forma observada | test_tasks.py | nº capturado, marca ausente |

## Acceptance criteria

- Testes novos e migrados de `test_pdf_utils.py` E `test_tasks.py` verdes.
- Suíte `apps/intake` e `apps/attachments` verdes.
- Quality gate completo: `ruff check`, `ruff format --check`, `mypy`,
  `pytest`.
- `strip_watermark` sem nomeados devolve texto byte-idêntico + report zerado.

## Out of Scope

- Payload do evento (slice 002); tamanho da marca; regexes do nº de
  ocorrência; gate; `_normalize_whitespace`; settings.

## Allowed incidental files

Nenhum.

## Verification (RED → GREEN)

```bash
# RED: os testes novos listados em R4 falham antes da implementação
uv run pytest apps/intake/tests/test_pdf_utils.py apps/intake/tests/test_tasks.py -q
# GREEN + consumidores:
uv run pytest apps/intake apps/attachments -q
# Gate completo:
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

## Expected files

- apps/intake/pdf_utils.py
- apps/intake/tasks.py
- apps/intake/tests/test_pdf_utils.py
- apps/intake/tests/test_tasks.py
