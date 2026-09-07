# Proposal: intake-nir-upload

## Why

O domínio de casos existe (change 03) mas não há como um caso nascer: o NIR precisa enviar os PDFs do relatório de regulação (formato SESAB "RELATÓRIO DE OCORRÊNCIAS", podendo vir dividido em vários arquivos), declarar os tipos de procedimento solicitados e acompanhar o processamento. Este change entrega a porta de entrada do HMD: upload multi-PDF com declaração de tipos, extração assíncrona de texto no cluster `pdf` (PyMuPDF, remoção de marca d'água, extração do nº de ocorrência), gate de regulação adaptado ao domínio (documento fora do padrão fica retido para revisão do NIR, sem seguir ao pipeline) e "meus casos". É a adaptação da mecânica comprovada do `apps/intake` do ats-web ao domínio de hemodinâmica.

## What Changes

- **App `apps/intake`**: views/templates NIR (upload com declaração multi-select dos 13 tipos, meus casos, detalhe com documentos/trilha/comunicações), forms, urls.
- **`CaseDocument`** (novo model em `apps/cases`): os 1..N PDFs que compõem o relatório, ordenados; texto extraído concatenado nessa ordem vira `Case.extracted_text` (fonte única — lição ats-web). Distinto de anexos de evidência (change 10).
- **Campos novos no `Case`**: `extracted_text`, `manual_review_required`, `manual_review_reason` (o nº de ocorrência usa `agency_record_number` existente).
- **Serviço de criação atômico**: valida PDFs (content-type, limites por env), cria `Case(NEW)` + documentos + declaração (`set_declared_procedures` do change 03) na mesma transação, com evento.
- **Cluster `pdf` (django-q2)**: task `process_case_documents` — claim de lock (`worker_pdf`), FSM `NEW→PDF_EXTRACTING`, extração PyMuPDF na ordem, remoção de marca d'água, extração do nº de ocorrência, gate de regulação → `ANONYMIZING` ou **retido** em `PDF_EXTRACTING` com flag+motivo+evento; erro de extração → `FAILED` com motivo. Compose ganha `worker-pdf` + volume de media compartilhado.
- **Gate de regulação adaptado**: header "RELATÓRIO DE OCORRÊNCIAS" + sinais institucionais BA + seções operacionais (thresholds por env, defaults do ats-web) — funções puras reaproveitando os padrões de `pdf_utils`/`regulation_gate` do ats-web.
- **Revisão NIR do gate**: caso retido aparece nos meus casos; NIR **libera** (bypass com evento → `ANONYMIZING`) ou **reenvia documentos** (substitui PDFs → reprocessa).
- **Nenhum estado novo na FSM** (guardrail do change 03): retenção = permanecer em `PDF_EXTRACTING` com flag; liberação usa transição existente.

## Capabilities

### `intake-nir` (nova)

- Criação de caso com upload multi-PDF e declaração de tipos (atômica, validada).
- Extração assíncrona no cluster pdf (texto, marca d'água, nº de ocorrência, FSM).
- Gate de regulação (retém fora do padrão; nunca avança sozinho).
- Revisão NIR do gate (liberar/reenviar).
- Meus casos e detalhe (escopo por criador; documentos, trilha e comunicações visíveis).

## Impact

- Arquivos: `apps/intake/**` (views/forms/urls/templates/tests), `apps/cases/{models,services?}/+CaseDocument+campos` + migrations, `apps/intake/{pdf_utils,regulation_gate,tasks}.py`, `config/settings/*` (q2, media, env), `docker-compose*.yml` (+worker-pdf, media volume), `pyproject.toml` (django-q2, PyMuPDF), `.env.example`, `docs/adr/ADR-0006*`.
- FSM: usa transições existentes; nenhum estado novo.
- Consumidores futuros: 05 (anonimiza `extracted_text`), 06 (pipeline lê `extracted_text`/`agency_record_number`), 07/08 (filas).

## Non-goals

- Anonimização/Presidio (05), pipeline LLM/detecção/reconciliação (06).
- Anexos de evidência com OCR (10) — `CaseDocument` é o relatório, não anexo.
- Resultado final/confirmação de recebimento/reenvio corrigido como novo caso (09).
- Intercorrências e ações de doctor/scheduler (07/08); notificações (11).
- Recuperação de caso `FAILED` (terminal neste change; NIR cria novo caso — limitação registrada).
