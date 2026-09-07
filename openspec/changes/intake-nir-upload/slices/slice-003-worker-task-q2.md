# Slice 003: Cluster pdf (django-q2) + task de processamento

## Objetivo

O processamento assíncrono: settings do django-q2 com cluster `pdf`, task `process_case_documents` (lock do caso, FSM `NEW→PDF_EXTRACTING→ANONYMIZING` ou retenção do gate, nº de ocorrência, `FAILED` em erro), enqueue no serviço de criação (com fallback inline para dev/teste) e infraestrutura de compose (worker-pdf + volume de media).

## Contexto necessário (contexto zero)

- Slices 001–002 entregues: criação atômica (`create_case_with_documents` — ainda sem enqueue) e `pdf_utils`/`regulation_gate` puros.
- Change 03: transições `start_pdf_extraction`/`complete_pdf_extraction`/`fail_processing` (recebem `*, user=None, role=None`); locks `claim_case_lock/assert/release` (`CaseLockConflictError`); `agency_record_number/agency_record_extracted_at`; eventos canônicos em `apps/cases/events.py`.
- Referência (somente-leitura): `/projects/dev/ats-web/apps/intake/tasks.py` (`enqueue_pdf_extraction` → `q_options={"cluster": "pdf"}`) e `config/settings/base.py` do ats-web (`Q_CLUSTER` + `ALT_CLUSTERS`: pdf workers 2/timeout 180) + `docker-compose.dev.yml` (serviço `pdf_worker` com `Q_CLUSTER_NAME`).
- Design: `../design.md` D2 (q2 + inline flag), D4 (eventos novos), D5 (task idempotente por estado). Spec: Requirements "Extração assíncrona no cluster pdf" (3 cenários) e "Gate de regulação" (2 cenários).
- **Eventos novos** em `apps/cases/events.py`: `CASE_GATE_MANUAL_REVIEW`, `CASE_GATE_BYPASSED` (slice 005 usa), `CASE_EXTRACTION_COMPLETED`.

## Requisitos

- **R1** `pyproject.toml` + lock: `django-q2` pinado; `config/settings/base.py`: `INSTALLED_APPS` com `django_q` + `Q_CLUSTER` no formato do ats-web — **`ALT_CLUSTERS` DENTRO de `Q_CLUSTER`**: `{"name": "hmd", "ORM": True, "ALT_CLUSTERS": {"pdf": {"workers": 2, "timeout": 180}}}`; migrations do django_q aplicáveis em banco limpo; `test.py` sem qcluster real (tasks chamadas direto).
- **R2** `process_case_documents(case_id, user=None)`: **branch por estado** — `NEW` → `start_pdf_extraction` e processa; `PDF_EXTRACTING` (retido/reenviado) → **PULA o start** (transição exige source `NEW`) e processa; demais estados → log + no-op (idempotente; reexecução não duplica eventos). Claim de lock `context="worker_pdf"`, `role="system"`. Processa: extração na ordem dos `CaseDocument`; `extract_agency_record_number` do **texto bruto concatenado** (ANTES do strip); `strip_watermark` → `extracted_text`; nº → `agency_record_number`+`agency_record_extracted_at`; gate → ok: `complete_pdf_extraction` (→`ANONYMIZING`) + evento `CASE_EXTRACTION_COMPLETED` (payload com nº ocorrência e tamanho do texto); falha do gate: grava `manual_review_required=True`+`manual_review_reason` + evento `CASE_GATE_MANUAL_REVIEW` (fica em `PDF_EXTRACTING`); exceção de extração → `fail_processing(reason)` → `FAILED`; release do lock no `finally` (token do claim).
- **R3** `enqueue_case_processing(case)`: `async_task(..., cluster="pdf")` quando `INTAKE_RUN_TASKS_INLINE=False`; senão executa a task sincronamente. `create_case_with_documents` chama o enqueue após a transação (com `INTAKE_RUN_TASKS_INLINE=True` em dev/teste default, criação já processa inline).
- **R4** Settings/env: `INTAKE_RUN_TASKS_INLINE` (base `True`; `prod` força `False` se não definido — falha fechado p/ não processar inline em produção sem querer); `.env.example` documenta `INTAKE_*` + `Q_*` relevantes.
- **R5** Compose: serviço `worker-pdf` (`manage.py qcluster`, env `Q_CLUSTER_NAME=pdf`) no `docker-compose.dev.yml`; **volume de media compartilhado** entre `web` e `worker-pdf` (mesmo bind/volume em `MEDIA_ROOT`); `docker-compose.yml` declara o volume.
- **R6** Testes (task chamada diretamente — sem qcluster): caso com relatório padrão (fixture PDF gerada) → `ANONYMIZING` + `extracted_text` preenchido + nº em `agency_record_number` + eventos na ordem (PDF_EXTRACTING, EXTRACTION_COMPLETED); nº presente como marca d'água E como padrão → preservado (extraído do bruto); PDF corrompido → `FAILED` + motivo no payload; fora do padrão → retido em `PDF_EXTRACTING` com flag/reason/evento `CASE_GATE_MANUAL_REVIEW`; **caso retido reenviado (PDF_EXTRACTING) → task processa SEM start e chega a `ANONYMIZING`**; reexecução em caso já em `ANONYMIZING` → no-op sem novos eventos; task respeita lock (caso travado por outro ator → `CaseLockConflictError` propagada); inline=True criação já deixa processado.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `pyproject.toml`, `config/settings/{base,test}.py`, migrations q2 | `uv run pytest` (migrations aplicáveis) |
| R2 | `apps/intake/tasks.py` | `test_tasks.py::test_happy_path_anonymizing`, `::test_corrupt_pdf_failed`, `::test_gate_retains`, `::test_idempotent_noop`, `::test_respects_lock`, `::test_resubmitted_case_reprocesses_without_start` (caso retido reenviado → task roda sem `start_pdf_extraction` e chega a `ANONYMIZING`) |
| R3 | `apps/intake/tasks.py`, `apps/intake/services.py` | `::test_inline_creation_processes` |
| R4 | `config/settings/{base,prod}.py`, `.env.example` | `rg -n "INTAKE_RUN_TASKS_INLINE" config/ .env.example` |
| R5 | `docker-compose.yml`, `docker-compose.dev.yml` | `docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet` |
| R6 | `apps/intake/tests/test_tasks.py` | `uv run pytest apps/intake/tests/test_tasks.py` |

## RED

- Comando: `uv run pytest apps/intake/tests/test_tasks.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.intake.tasks'`.

## GREEN / verificação local

- `uv run pytest apps/intake/tests/test_tasks.py` — exit 0
- `uv run pytest apps/intake/tests/ apps/cases/tests/` — exit 0
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/intake/tasks.py
  - apps/intake/services.py           # + enqueue pós-transação
  - apps/intake/tests/test_tasks.py
  - apps/cases/events.py              # +3 tipos canônicos
  - config/settings/{base,prod,test}.py
  - pyproject.toml
  - uv.lock
  - docker-compose.yml
  - docker-compose.dev.yml
  - .env.example
allowed_incidental_files:
  - apps/intake/tests/conftest.py (helpers de fixture PDF reutilizados do 002)
out_of_scope:
  - ações de revisão do gate (slice 005)
  - meus casos/detalhe (slice 004)
  - cluster llm (change 06)
  - agendamento/limpeza de tasks antigas
```

Escale ao parent se: o django-q2 exigir configuração de broker além de ORM; o volume de media exigir mudanças em settings de storage.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz (5 cenários das 2 specs cobertos)
- [ ] Task idempotente: reexecução nunca duplica eventos
- [ ] Lock respeitado; release no finally
- [ ] Compose valida com worker-pdf + media compartilhada
- [ ] Gate parcial do slice verde
