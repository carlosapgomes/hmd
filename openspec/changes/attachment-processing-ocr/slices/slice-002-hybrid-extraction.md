# Slice 002: Extração híbrida (local/vision) + worker cluster attachments

## Objetivo

Cada anexo pendente é processado de forma assíncrona após a anonimização do
relatório principal: PDF com camada de texto → PyMuPDF local; foto/scan/
PDF-imagem → OCR externo via `VISION_MODEL` (com evento de auditoria antes
do envio); falha → `failed` com motivo, caso nunca bloqueado.

## Contexto necessário

- Slice 001 entregue (`CaseAttachment` existe com `status`/`extracted_text`).
- Design D3/D7 (`openspec/changes/attachment-processing-ocr/design.md`).
- `apps/intake/pdf_utils.py` — PyMuPDF (fitz) já é dependência; reutilize os
  helpers de extração de texto por página onde possível (padrão do worker
  pdf do change 04).
- `apps/pipeline/llm.py` — `LlmError` (reutilizar; NÃO estender o protocolo
  `LlmClient`); `settings.OPENROUTER_*`/`LLM_TIMEOUT_SECONDS` (mesma
  base/env do cliente do pipeline).
- Worker pattern: `apps/intake/tasks.py` (cluster pdf, inline flag,
  `async_task` signature) e `apps/anonymization/tasks.py` (fail-closed por
  caso). Settings: `Q_CLUSTER["ALT_CLUSTERS"]` + flags `*_RUN_TASKS_INLINE`
  em `config/settings/base.py`.
- Trigger pattern: `apps/pipeline/signals.py` — `CaseEvent.post_save`
  filtrando `event_type == CASE_ANONYMIZATION_COMPLETED` +
  `transaction.on_commit` (leia-o por completo; replique em
  `apps/attachments/signals.py`; o filtro `payload["source"]` anti-recursão
  não se aplica aqui — o evento de anonimização não é emitido pelo worker de
  anexos).
- Eventos canônicos a criar em `apps/cases/events.py`:
  `CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED`, `CASE_ATTACHMENT_FAILED` (o
  `CASE_ATTACHMENT_PROCESSED` entra no slice 003 — a extração pura ainda não
  produz resultado de verificação; gravar `CASE_ATTACHMENT_FAILED` em falha
  da EXTRAÇÃO).
- Rasterização: fitz `page.get_pixmap()` → PNG bytes (teto
  `ATTACHMENTS_VISION_MAX_PAGES=10` páginas por anexo; excedente →
  `failed` com motivo "excede teto de páginas").

## Requisitos verificáveis

- **R1** `apps/attachments/extraction.py::extract_attachment_text(attachment)
  -> tuple[str, str]` (texto, método): PDF com texto (soma das páginas >
  30 chars) → `local_pdf` (PyMuPDF, sem chamada externa); imagem OU PDF sem
  texto → rasteriza (PDF) e chama `vision.transcribe_image` por página
  (concatena com separador `\n\n`), método `vision`.
- **R2** `apps/attachments/vision.py::transcribe_image(image_bytes,
  content_type) -> str`: SDK OpenAI (`OPENROUTER_API_KEY`/`BASE_URL`/
  timeout do settings), `settings.VISION_MODEL`, message multimodal
  content com image data-URL; sem `VISION_MODEL` configurado →
  `LlmError("config", ...)` fail-closed ANTES de qualquer envio; erros da
  API mapeados em `LlmError` tipado (padrão do `OpenRouterClient`).
- **R3** Task `process_case_attachments(case_id)` no cluster `attachments`
  (`async_task` com `cluster="attachments"`; flag
  `ATTACHMENTS_RUN_TASKS_INLINE` default true): idempotente **por etapa**
  (anexo com `extracted_text`/`extraction_method` já presentes pula a
  extração — retry do q2 NUNCA re-envia ao OCR externo nem duplica evento de
  auditoria; re-leitura da row antes de cada gravação/evento;
  `CaseAttachment.DoesNotExist` → no-op silencioso SEM evento — corrida com a
  ciência do NIR); para cada anexo `pending`/`processing`: `processing` →
  extração → persiste `extracted_text`/`extraction_method` (status permanece
  `processing` — a verificação do slice 003 fecha como `processed`);
  **evento de auditoria `CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED` (actor
  system, payload filename+método) ANTES do envio externo**; falha por
  anexo → `failed` + `failed_reason` + `CASE_ATTACHMENT_FAILED`, sem
  afetar os demais anexos nem o caso (fail-closed por anexo).
- **R4** Trigger: `apps/attachments/signals.py` — `CaseEvent.post_save`
  filtra `CASE_ANONYMIZATION_COMPLETED` → `on_commit` →
  `async_task(process_case_attachments)` (rollback não enfileira; caso sem
  anexos: task retorna imediato). Registrar o app em `INSTALLED_APPS` já
  cobre o `ready()` (apps.py do padrão `apps.pipeline`).
- **R5** Settings: cluster `attachments` em `ALT_CLUSTERS` (espelha os
  existentes), `ATTACHMENTS_RUN_TASKS_INLINE`, `ATTACHMENTS_VISION_MAX_PAGES`;
  novos envs (`VISION_MODEL`, `ATTACHMENTS_*`) documentados em
  `.env.example` (política do AGENTS.md).
- **R6** Testes: extração local (PDF com texto; SEM evento externo);
  imagem → vision chamado com bytes+data-URL + evento de auditoria ANTES;
  PDF-imagem → rasterização+vision; `VISION_MODEL` ausente → fail-closed
  sem envio; falha de vision → `failed`+evento, caso e outros anexos
  intactos; teto de páginas; **idempotência por etapa** (anexo já com
  `extracted_text` re-executado → SEM nova chamada de vision/evento); **anexo
  deletado no meio (DoesNotExist) → no-op sem evento**; trigger dispara no
  `CASE_ANONYMIZATION_COMPLETED` via on_commit (precedente de teste:
  `apps/pipeline/tests/test_signals.py` com `django_db(transaction=True)`);
  inline flag respeitada (`override_settings(ATTACHMENTS_RUN_TASKS_INLINE=False)`
  + assert de enqueue). Vision/LLM sempre com fakes injetáveis (monkeypatch)
  — suíte sem rede.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/attachments/extraction.py` | `test_extract_pdf_text_local`, `test_extract_image_via_vision`, `test_extract_pdf_image_via_vision`, `test_extract_page_cap_failed` |
| R2 | `apps/attachments/vision.py` | `test_vision_no_model_fails_closed`, `test_vision_transcribes_image` (fake SDK) |
| R3 | `apps/attachments/tasks.py` | `test_task_processes_pending`, `test_task_retry_skips_extraction`, `test_task_attachment_deleted_noop`, `test_task_vision_failure_marks_failed_other_attachments_intact` |
| R4 | `apps/attachments/signals.py`, `apps/attachments/apps.py` | `test_signal_enqueues_after_anonymization` (transaction=True), `test_no_enqueue_on_rollback` |
| R5 | `config/settings/base.py` | asserts de settings no conftest/test |
| R6 | `apps/attachments/tests/` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/attachments/{extraction,vision,tasks,signals,apps}.py
  - apps/attachments/tests/{conftest,test_extraction.py,test_tasks.py,test_signals.py}
  - .env.example                          # VISION_MODEL + ATTACHMENTS_* (P2 review)
  - apps/cases/events.py                 # +3 eventos canônicos (2 usados aqui)
  - config/settings/base.py              # cluster + flags + VISION_MODEL + page cap

out_of_scope:
  - anonimização/verificação do texto extraído (slice 003 — status "processing" persiste)
  - UI (nenhuma neste slice); apps/cases/models.py; orchestrator do change 06
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/attachments/tests/test_tasks.py`
- Falha esperada: `ImportError: cannot import name 'process_case_attachments'`.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/attachments/tests/
  apps/anonymization/tests/ apps/intake/tests/` — exit 0 (regressão do
  worker de anonimização + intake).
- `uv run ruff check apps/attachments apps/cases config &&
  uv run ruff format --check apps/attachments apps/cases config`
- `uv run mypy .`

## Critérios de aceitação

- [ ] R1–R6 comprovados; evento de auditoria ANTES do envio externo (testado)
- [ ] Falha de extração nunca bloqueia caso nem outros anexos
- [ ] Suíte sem rede (fakes para vision); inline flag + on_commit testados
- [ ] Gate parcial do slice verde
