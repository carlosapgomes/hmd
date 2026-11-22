# Proposal

## Why

Os relatórios de regulação que solicitam exames ou transferências declaram, no
bloco do relatório médico, o tipo de transporte solicitado (`TRANSPORTE:`
→ `Ambulância Básica` ou `UTI Móvel`). É um sinal operacional relevante —
`UTI Móvel` indica maior gravidade/recursos — mas hoje vive enterrado no texto
do PDF: NIR, médico e agendador precisam abrir o documento para encontrá-lo.
Verificação no corpus real (9 PDFs, `/projects/dev/hmd/temp/samples/`): o label
estruturado aparece em 9/9 documentos, capturável por regex linha-âncorado sobre
o texto do PyMuPDF.

## What Changes

- Extração determinística do label `TRANSPORTE:` no worker de PDF (mesmo molde
  dos metadados do cabeçalho SESAB — `extract_header_metadata`), sobre o texto
  já limpo, no mesmo write do `extracted_text`.
- Novo campo `Case.transport_type` (domínio fechado `basic` | `mobile_icu`,
  nulável): normalização tolerante a acento/case — `Ambulância Básica` e a
  forma abreviada `BÁSICA` → `basic`; `UTI Móvel` → `mobile_icu`. Valor
  desconhecido ou label ausente → campo vazio (nunca inventado).
- Badge de transporte nos **cards de caso das três filas**: meus casos (NIR),
  fila médica (doctor) e fila do agendador (scheduler) — `UTI Móvel` com ênfase
  visual (severity), `Ambulância Básica` neutra; sem valor, sem badge.
- Transporte exibido no **detalhe do caso médico** (card de identificação,
  junto da demografia) — dado determinístico, não passa pelo LLM.
- **Sem** mudanças no pipeline LLM: prompts, schemas, policy e fronteira de
  anonimização permanecem intactos (o valor não é PII; a extração é anterior e
  independente da anonimização).
- **Ciclo de vida:** metadado administrativo não-identificante — preservado na
  minimização do `CLEANED` (molde dos metadados do cabeçalho); zerado apenas
  pelo reenvio de documento (worker reatribui a cada extração).

## Capabilities

### New Capabilities

(nenhuma — a mudança estende capacidades existentes)

### Modified Capabilities

- `intake-nir`: a extração no cluster pdf passa a extrair também o tipo de
  transporte do label `TRANSPORTE:` do relatório médico (determinístico,
  domínio fechado, valor ausente/desconhecido não inventa); os cards de «meus
  casos» exibem o badge de transporte.
- `doctor-decision`: os cards da fila médica exibem o badge de transporte; o
  detalhe do caso (presenter re-identificado) exibe o transporte no card de
  identificação.
- `scheduling`: os cards da fila do agendador exibem o badge de transporte.

## Impact

- **Código:** `apps/intake/pdf_utils.py` (função pura de extração +
  normalização), `apps/intake/tasks.py` (persistência no write existente),
  `apps/cases/models.py` (+ migration aditiva do campo), views das filas
  (`apps/intake/views.py`, `apps/doctor/views.py`, `apps/scheduler/views.py`)
  e templates dos cards (`templates/intake/_my_cases_content.html`,
  `templates/doctor/_case_cards.html`, `templates/scheduler/_queue_content.html`,
  `templates/doctor/case_detail.html`).
- **Reprocessamento:** reenvio de documento e reenvio corrigido re-extraem e
  regravam o campo no mesmo fluxo existente (worker sempre reatribui os
  metadados — sem estado stale).
- **Sem impacto:** pipeline LLM, anonimização, FSM, eventos (o valor não vai
  para eventos, no molde dos metadados do cabeçalho), API externa, migrações
  destrutivas (nenhuma).
- **Fixtures:** os 9 PDFs do corpus real validam o regex (100% de captura na
  verificação prévia; amostras de `BÁSICA` abreviado cobertas).
