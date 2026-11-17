# Design: watermark-strip-hardening

## Context

- `strip_watermark` (`apps/intake/pdf_utils.py:68`): nomeia token só por banda
  intra-linha cheia (`_REPEATED_FIVE_DIGIT_LINE_PATTERN =
  r"^\s*(\d{5,6})(?:\s+\1){3,}\s*$"`, `count >= 1` na nomeação) → remove
  linhas de banda → sweep global `\b{token}\b` (passo 3,
  `_DIGIT_TOKEN_PATTERN`) → `_normalize_whitespace`. Early-return sem
  candidatos (texto byte-idêntico, R2 testado).
- **Premissa de domínio corrigida** (entrada do usuário, observação de
  produção): a marca **quase nunca** coincide com o nº de ocorrência. A
  coincidência era suposição persistida nas implementações e fixtures — as
  fixtures canônicas de banda única do próprio nº (`test_tasks.py:37-47`,
  `test_pdf_utils.py:100-117,159-180`) codificam o caso raro e migram para a
  forma observada (marca ≠ nº, múltiplas linhas de marca).
- Ordem D3/R5b (`apps/intake/tasks.py:228-229`): nº extraído do bruto ANTES
  do strip; `extracted_text` é o texto limpo.
- `extract_document_text` (`pdf_utils.py:47`, join em `:59`): `"".join` das
  páginas. Verificação empírica: o `get_text()` do PyMuPDF **sempre** termina
  linha (inclusive a última da página) com `\n` — a fusão de linhas não
  ocorre hoje com PyMuPDF real; o `"\n".join` é endurecimento de contrato.
- Caller produtivo único de `strip_watermark`: a task (`tasks.py:229`; demais
  call sites são testes). **Consumidor adicional** de
  `extract_document_text`: `apps/attachments/extraction.py:31,78,112` decide
  local×OCR pelo limiar `len(text) > 30` — o `\n` extra por página só soma
  chars (tende ao caminho local, sem OCR).
- Comparáveis: ats-web nomeia por frequência ≥11 (recall alto, bug do ano em
  3+ dígitos); sarv3 remove linha declarada (grade 6+ repetições; diagonal =
  1–6 repetições na linha, só 1ª página) com `RemovedLines` auditável. A
  forma "diagonal" do sarv3 (poucas repetições por linha) NÃO casa o padrão
  de banda cheia do HMD — é parte do gap de recall.
- Evento `CASE_EXTRACTION_COMPLETED` (`intake/tasks.py`, payload enxuto,
  `CaseEvent.payload` é `JSONField` — precisa de dict plano).

## Goals / Non-Goals

**Goals:**

- Recall: capturar marca em linha de marca com 1..N repetições (linha solta,
  diagonal com poucas repetições, banda cheia), mantendo 5–6 dígitos.
- Precisão: linha única não nomeia; token ≠ nº de ocorrência não é varrido
  de linha mista (dano de falso positivo limitado a linhas inteiras).
- Robustez: fronteira de página explícita na concatenação.
- Observabilidade: contagens da remoção no evento de extração (sem valores).

**Non-Goals:**

- Mudar o tamanho da marca (5–6 dígitos permanece; guardas de contexto de
  data/separador só voltam a importar se um dia baixar).
- Nomeação por frequência livre (estilo ats-web) ou perfil declarado (sarv3).
- Mexer no nº de ocorrência regexes (`[0-9]{5,}` inalterado), gate de
  regulação, anonimização, pipeline LLM, modelos/migrações.
- Normalizar texto sem marca (early-return byte-idêntico permanece).
- Telemetria no evento de retenção do gate (`CASE_GATE_MANUAL_REVIEW`): as
  contagens vivem apenas no evento de conclusão (contrato da spec).
- Varrer o nº de ocorrência inline quando ele não forma linhas de marca
  (status quo: quando marca ≠ nº — o caso comum — o nº citado em "Código:"
  permanece no texto e é tokenizado pela anonimização determinística
  downstream).

## Decisions

### D1 — Nomeação unificada: linha de marca com ≥2 ocorrências

**Linha de marca** = linha composta exclusivamente de repetições (1..N,
separadas por whitespace) de um mesmo token de 5–6 dígitos — padrão
`^\s*(\d{5,6})(?:\s+\1)*\s*$`, que unifica banda cheia (4+), diagonal com
poucas repetições (2–3, forma do sarv3) e linha solta (1). Token é nomeado
com **≥2 linhas de marca** no documento (`_MIN_MARK_LINES = 2`, constante
módulo-nível; sem setting novo — valor do corpus, não operacional).

Racional: com a realidade "marca ≠ nº" e o sweep assimétrico (D2), o dano de
um falso positivo cai para "linhas inteiras removidas" (não mais valores
inline destruídos) — o que permite um limiar baixo (≥2, igual ao recall de
banda do status quo) sem herdar o risco do sweep destrutivo antigo. A
exigência de ≥2 linhas guarda contra a linha esporânea isolada de tabela
(valores iguais renderizados numa linha) e é validada por observação de
domínio: mesmo relatórios de página única têm ao menos 2 linhas com a marca —
marca de linha única não ocorre no corpus.

Alternativas: frequência ≥N estilo ats-web (rejeitada — raiz do bug do ano);
perfil declarado sarv3 (rejeitado — não descobre marca nova); limiar ≥1
(rejeitado — nomeia qualquer valor solto isolado em linha); exceção especial
para token == nº (rejeitada — codificaria o caso raro como canônico; a
coincidência, quando ocorre, já é coberta pela regra geral ≥2 + sweep D2).

### D2 — Sweep assimétrico por coincidência com o nº de ocorrência

Após nomear e descartar as linhas de marca: se o token nomeado coincide com
o `record_number` extraído do bruto (parâmetro explícito
`record_number: str | None` em `strip_watermark`), remove TODAS as
ocorrências delimitadas `\b{token}\b` (inline incluso — ocorrência colada a
letras ou dentro de run de 7+ dígitos não casa `\b` e permanece;
comportamento do regex, documentado aqui); tokens nomeados ≠ nº NÃO têm
sweep inline — só as linhas de marca já descartadas. Filtro redundante do
passo 3 (`>= 1`) some.

Racional: quando a marca coincide com o nº (raro), o sweep global do nº é
deterministicamente desejável (o valor vive no caso). Para qualquer outro
token, ocorrência inline é mais provavelmente valor clínico legítimo (CBO
"225135", protocolo) do que rastro de marca — precisão > recall de rastro,
especialmente porque o texto segue anonimizado e o guard `_assert_tokens_only`
cobre PII do mapa, não marca.

Alternativa: sweep global para todos (status quo) — rejeitado: é o dano que
este change elimina.

### D3 — Relatório de retorno: semântica fechada das contagens

`strip_watermark(text, *, record_number=None)` devolve
`WatermarkStripResult(text, report)` — dataclasses imutáveis;
`WatermarkReport` com `tokens: int` (tokens distintos nomeados),
`mark_lines: int` (linhas de marca descartadas) e `swept_occurrences: int`
(apenas ocorrências inline removidas pelo sweep do D2 — exclui o conteúdo
das linhas descartadas). Sem nomeados → report zerado e texto idêntico
(early-return). Função permanece pura; serialização para `JSONField` via
`dataclasses.asdict` (slice 002).

Alternativa: manter `str` e computar contagens na task por diff (rejeitado:
frágil e duplica lógica); contagens separadas por formato de linha
(banda/solta) — rejeitadas: a unificação D1 torna a distinção artificial.

### D4 — Separador de página explícito (endurecimento, não bugfix)

`extract_document_text`: `"\n".join(page.get_text() ...)`. Como o PyMuPDF
hoje sempre `\n`-termina linhas (verificado empiricamente), não há bug
produzido hoje — o join explícito remove a dependência de comportamento não
contratado do extrator. O teste de fronteira NÃO pode ser RED via PDF real
(o `write_pdf` nunca produz página sem `\n` final); a regressão trava o
comportamento com dublê no seam do `pymupdf.open` (documento falso com
páginas cujo `get_text()` não termina em `\n`) — RED no join atual (linhas
fundidas → linha de marca não casa → token sobrevive), GREEN com
`"\n".join`.

Impacto do `\n` extra: no máximo um `\n` entre páginas para consumidores
line-based (gate, header metadata, strip — benigno) e no limiar local×OCR de
attachments (só soma chars; tende ao caminho local). Suíte de attachments na
verificação do slice 001.

### D5 — Testes: fixturar os layouts reais + colisões + migração canônica

`test_pdf_utils.py` (unidade): 2 linhas de marca removem (banda cheia ×2,
linha solta ×2, e forma diagonal "48291 48291" + "48291" mista); linha única
preserva; inline ≠ nº preservado; token == nº (2 linhas) varre inline;
contagens do D3 conferem; fronteira de página via dublê do `pymupdf.open`
(D4); report zerado + texto idêntico sem nomeados; migração das fixtures
canônicas de banda única (dobrar a linha ou passar `record_number=`
conforme o que cada teste pinna). `test_tasks.py` (integração): fixture
canônica MIGRA para a forma observada (token de marca ≠ nº, ex. "48291" em
2 linhas de marca; "Código: 33345" permanece e o nº é capturado no campo) —
as asserções passam a refletir a realidade: marca ausente do texto, nº
presente em `agency_record_number` (e citado inline permanece no texto,
tokenizado depois pela anonimização).

## Risks / Trade-offs

- [Falso positivo residual: valor legítimo de 5–6 dígitos formando linha de
  marca 2× (ex.: código repetido em linhas isoladas de 2 páginas)] → Dano
  limitado às linhas inteiras (D2); aceito; mitigável elevando
  `_MIN_MARK_LINES` sem quebrar contrato.
- [Perda de recall: marca com UMA única linha de marca no documento] →
  Fechado por observação de domínio: não ocorre (mesmo página única tem ≥2
  linhas de marca). O ≥2 protege contra linha esporânea de tabela sem custo
  de recall real.
- [Comportamento visível muda para o caso raro marca == nº com UMA linha
  (hoje: removida; depois: permanece)] → Consequência da premissa corrigida
  e do limiar uniforme; o caso raro com ≥2 linhas segue com sweep global (D2).
- [Perda de rastro inline de marca não-nº] → Trade-off deliberado (D2);
  texto segue anonimizado; custo de destruir valor clínico é maior.
- [Mudança de assinatura do `strip_watermark` quebra callers] → Caller
  produtivo único é a task (grep-verificado); atualizado no slice 001 com
  transição mínima; fixtures de teste migradas no mesmo slice.
- [`extracted_text` ganha `\n` entre páginas; limiar local×OCR de
  attachments no entorno de 30 chars pode virar decisão] → Direção benigna
  (mais chars → caminho local, sem OCR externo); suíte de attachments na
  verificação do slice 001 como rede de segurança.
- [Dataclass não serializa em `JSONField`] → `dataclasses.asdict` mandatório
  no slice 002 (D3).

## Migration Plan

Change único, sem dados/migrações: deploy normal dos slices; rollback = revert
do commit (casos já extraídos mantêm o texto que têm — `extracted_text` não é
recomputado retroativamente).

## Open Questions

(nenhuma)
