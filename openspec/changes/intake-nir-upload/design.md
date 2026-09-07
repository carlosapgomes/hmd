# Design: intake-nir-upload

## Contexto

Adaptação da mecânica do `apps/intake` do ats-web (recon `temp/research/ats-web-recon.md` itens 3, 6, 11; nota do recon: "clone hmd deve trocar o gate por um próprio do domínio") ao domínio de hemodinâmica, consumindo o núcleo do change 03 (FSM/procedures/locks/eventos). Roadmap §11 row 04: "Upload multi-PDF, cluster pdf, PyMuPDF + watermark/nº ocorrência, regulation gate adaptado, declaração de tipos (multi-select), meus casos". Dono confirmou: LLM só verá texto anonimizado (05/06); médico verá o PDF original (07); extração determinística do nº de ocorrência.

### D1 — `CaseDocument`: multi-PDF do relatório

O relatório SESAB pode chegar dividido em vários arquivos (scanner). `CaseDocument`: `case FK PROTECT` (docs não órfãos), `file` (storage `case_documents/<case_id>/<uuid>.pdf`), `position` (ordem), `original_filename`, `content_type`, `size_bytes`, `uploaded_by`, `created_at`; constraint `(case, position)` única; apenas `application/pdf`. O texto é extraído **na ordem** e concatenado em `Case.extracted_text` (fonte única — lição do ats-web; anexos de evidência com OCR são o change 10 e NÃO são `CaseDocument`). Valores default por env: `INTAKE_MAX_DOCUMENTS=10`, `INTAKE_MAX_FILE_MB=20`.

### D2 — Cluster `pdf` (django-q2)

Mesma stack do ats-web: `django-q2` pinado; `Q_CLUSTER` base com `name="hmd"` e `ALT_CLUSTERS = {"pdf": {workers: 2, timeout: 180}}` (cluster `llm` chega no change 06). Enqueue com `q_options={"cluster": "pdf"}`. Compose dev ganha serviço `worker-pdf` (`manage.py qcluster`, `Q_CLUSTER_NAME: pdf`) e **volume de media compartilhado** entre `web` e `worker-pdf` (filesystem local; sem S3 — plano §3). `test.py`/dev sem worker: flag `INTAKE_RUN_TASKS_INLINE` (default `True` em dev/teste, `False` em prod/compose com worker) — o serviço de criação executa a task **sincronamente** quando inline (UX imediata e testes determinísticos); com worker real, enfileira.

### D3 — Extração: PyMuPDF + marca d'água + nº de ocorrência

`apps/intake/pdf_utils.py` adaptado do ats-web: `extract_document_text(path)` (PyMuPDF pinado via uv — versão exata travada no lockfile), `strip_watermark(text)` (sequência de 5–6 dígitos do registro que se repete no documento), `extract_agency_record_number(text)` (padrões "Código: XXXXX" e "RELATÓRIO DE OCORRÊNCIAS … XXXXX"; fallback = None). O nº vai para `Case.agency_record_number` + `agency_record_extracted_at` (campos existentes do change 03). Funções puras — testáveis com PDFs de fixture **gerados pelo próprio PyMuPDF** nos testes (sem binários no repo).

### D4 — Gate de regulação adaptado (função pura + retenção sem estado novo)

`apps/intake/regulation_gate.py`: `evaluate_regulation_report(text) -> GateResult(ok, reason)` — cabeçalho "RELATÓRIO DE OCORRÊNCIAS", sinais institucionais da Bahia e seções operacionais, com thresholds por env (`INTAKE_REGULATION_MIN_TEXT_CHARS=500`, `INTAKE_REGULATION_MIN_OPERATIONAL_SECTIONS=3` — defaults do ats-web). Falha → caso **retido**: permanece em `PDF_EXTRACTING` com `manual_review_required=True` + `manual_review_reason` (campos novos no `Case`, migration incremental) + evento `CASE_GATE_MANUAL_REVIEW`; **nunca avança sozinho**. Bypass (revisão NIR) usa a transição existente `complete_pdf_extraction` + evento `CASE_GATE_BYPASSED` — **nenhum estado novo** (guardrail do change 03). Novos tipos canônicos em `apps/cases/events.py`: `CASE_GATE_MANUAL_REVIEW`, `CASE_GATE_BYPASSED`, `CASE_EXTRACTION_COMPLETED`.

### D5 — Task do worker com lock e FSM

`apps/intake/tasks.py::process_case_documents(case_id, user=None)`: claim de lock (`context="worker_pdf"`, `role="system"`, lease `CASE_LOCK_LEASE_SECONDS`) — integração consumer prevista na D6 do change 03; `start_pdf_extraction` (NEW→PDF_EXTRACTING, evento); extração na ordem + watermark + nº; gate → `complete_pdf_extraction` (→ANONYMIZING, evento `CASE_EXTRACTION_COMPLETED`) **ou** retenção (D4); erro de extração (exceção PyMuPDF) → `fail_processing` com motivo → `FAILED`; release do lock no finally. Task **idempotente por estado**: caso já fora de `NEW/PDF_EXTRACTING` → no-op com log (reexecução do q2 não duplica eventos). Enqueue no serviço de criação (inline/async conforme D2).

### D6 — Views/forms NIR (authz)

`apps/intake/`: `intake_home` (form upload + multi-select de tipos a partir do catálogo via `procedure_catalog`), `my_cases` (filtro `created_by=request.user`, indicador de retenção), `case_detail` (documentos com `serve_document` seguro — sem path traversal, content-type do storage; trilha de eventos; comunicações; ações do gate quando retido), `gate_release`/`gate_resubmit` (POST). Todas `@role_required("nir")` + escopo por criador (404 para caso alheio). Papel ativo vem da sessão (`apps/accounts`); guard de intranet já cobre `nir` globalmente. `gate_resubmit`: substitui documentos (novos `CaseDocument`, antigos removidos), zera flag/texto/nº, reenfileira processamento — disponível apenas para caso retido.

### D7 — Serviço de criação atômico

`apps/intake/services.py::create_case_with_documents(*, user, files, procedure_types)`: valida tipos contra o catálogo e arquivos (PDF-only, count/size por env) **antes** de qualquer persistência; numa transação: `Case(NEW)` + `CaseDocument` rows + `set_declared_procedures` (change 03, com evento); fora da transação: enqueue/inline da task (D2). Erro em qualquer validação → nada persiste, erro nomeia arquivo/tipo.

### D8 — ADR-0006

`docs/adr/ADR-0006-intake-multi-pdf-gate-cluster.md`: decisão (CaseDocument multi-PDF; retenção por flag sem estado novo; cluster pdf com inline fallback; gate adaptado ao domínio), alternativas (single-PDF como ats-web; estado NIR_REVIEW dedicado; gate no pipeline pós-LLM como ats-web), consequências (extração é a única fonte de texto; reenvio substitui docs; FAILED é terminal neste change).

### D9 — Sem antecipação

Campos `extracted_text`/`manual_review_*` chegam agora (consumidos aqui); `structured_data`/`summary_text`/anônimos ficam para 05/06; dashboard/filtros avançados para 11. `serve_document` serve apenas NIR criador neste change (doctor/manager ganham acesso nos changes 07+).

## Riscos e mitigações

- **PDFs de foto/scan sem camada de texto** (dono confirmou que existem): extração retorna texto vazio/curto → gate retém por `MIN_TEXT_CHARS` → revisão NIR. Caminho de OCR é o change 10 — documentado no ADR.
- **Worker e web com media dessincronizada**: volume compartilhado no compose; docstring registra requisito de deploy.
- **Reexecução dupla da task (q2 retry)**: idempotência por estado (D5) + lock.

## Fora de escopo

Anonimização (05), LLM/detecção (06), filas doctor/scheduler (07/08), resultado/confirmação/reenvio-corrigido (09), OCR de anexos (10), notificações (11).
