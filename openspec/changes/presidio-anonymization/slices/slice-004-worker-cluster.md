# Slice 004: Cluster anonymization + task + signal + ADR

## Objetivo

O processamento assíncrono com fail-closed: cluster `anonymization` no django-q2, task `process_case_anonymization` (idempotente por estado, lock, FSM `ANONYMIZING → LLM_EXTRACTING` ou `FAILED`), trigger por signal na entrada de `ANONYMIZING` (sem tocar no intake), compose `worker-anonymization` e o ADR-0007.

## Contexto necessário (contexto zero)

- Slices 001–003 entregues: deterministic, engine, serviço `anonymize_case_text` (+campos/evento).
- Change 03: transições `start_anonymization` (self em ANONYMIZING), `complete_anonymization` (→LLM_EXTRACTING), `fail_processing`; locks (`CaseLockConflictError`); signal `CaseEvent.post_save` já usado pelas comunicações (`apps/cases/signals.py` — padrão a espelhar no AppConfig do anonymization).
- Change 04: `ALT_CLUSTERS` em `Q_CLUSTER` (cluster `pdf`); `INTAKE_RUN_TASKS_INLINE` (padrão do inline flag); eventos `CASE_STATUS_*`; task pdf idempotente como referência interna (`apps/intake/tasks.py`).
- Pesquisa §10 (django-q2 worker de anonimização: workers 2, timeout 300, retry 360; singleton; não carregar modelo no web).
- Design: `../design.md` D7 (trigger desacoplado por signal), D10 (ADR). Spec: Requirements "Fail-closed" (cenário bloqueio) e "Processamento assíncrono" (2 cenários).

## Requisitos

- **R1** `Q_CLUSTER["ALT_CLUSTERS"]["anonymization"] = {"workers": 2, "timeout": 300, "retry": 360}`; `ANONYMIZATION_RUN_TASKS_INLINE` (base/teste `True`, prod `False`) + `.env.example`.
- **R2** `enqueue_case_anonymization(case_id)`: inline/async por flag (`q_options={"cluster": "anonymization"}`).
- **R3** Signal: receiver `CaseEvent.post_save` (registrado no `AppsConfig.ready` do anonymization) para `event_type == CASE_STATUS_ANONYMIZING` → `enqueue_case_anonymization(event.case_id)`; casos que chegam a `ANONYMIZING` por QUALQUER path do change 04 disparam a task (extração ok, gate liberado, reenvio reprocessado) sem alterar o intake.
- **R4** `process_case_anonymization(case_id)`: idempotente por estado (apenas `ANONYMIZING`; demais no-op com log); claim lock `worker_anonymization`/`system` (release no finally); `start_anonymization` (evento de início); `anonymize_case_text` (slice 003); sucesso → `complete_anonymization` (→ `LLM_EXTRACTING`); exceção → `fail_processing(str(err))` → `FAILED` com motivo (**fail-closed** — `anonymized_text` permanece vazio).
- **R5** Compose dev: serviço `worker-anonymization` (`manage.py qcluster`, `Q_CLUSTER_NAME=anonymization`) — a imagem do worker instala o modelo spaCy no build (documentar no compose/ADR; mesma imagem base do worker-pdf + modelo). `docker compose config` valida.
- **R6** `docs/adr/ADR-0007-anonimizacao-presidio-fail-closed.md` completo (decisão/alternativas/consequências — design D10).
- **R7** Testes: caso em ANONYMIZING com texto → task → LLM_EXTRACTING com anonymized_text preenchido + eventos início/conclusão; exceção do serviço (monkeypatch) → FAILED + motivo + anonymized_text vazio; reexecução em LLM_EXTRACTING → no-op; lock ativo → `CaseLockConflictError`; signal dispara enqueue ao gravar evento CASE_STATUS_ANONYMIZING (assert com inline); caso ANONYMIZING com extracted_text vazio → comportamento definido (serviço defensivo do 003 → avança com texto vazio? **NÃO** — falha fechada: caso sem texto em ANONYMIZING → fail_processing("sem texto extraído") — teste).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `config/settings/{base,prod,test}.py`, `.env.example` | `rg -n "anonymization" config/settings/base.py .env.example` |
| R2/R4 | `apps/anonymization/tasks.py` | `test_tasks.py::test_happy_path_llm_extracting`, `::test_service_exception_fails_closed`, `::test_idempotent_noop`, `::test_respects_lock`, `::test_empty_text_fails_closed` |
| R3 | `apps/anonymization/{signals,apps}.py` | `::test_signal_enqueues_on_anonymizing_entry` |
| R5 | `docker-compose.dev.yml`, `docker-compose.yml` | `docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet` |
| R6 | `docs/adr/ADR-0007*.md` | inspeção de seções |
| R7 | `apps/anonymization/tests/test_tasks.py` | `uv run pytest apps/anonymization/tests/test_tasks.py` |

## RED

- Comando: `uv run pytest apps/anonymization/tests/test_tasks.py`
- Falha esperada: `ModuleNotFoundError: apps.anonymization.tasks`.

## GREEN / verificação local

- `uv run pytest apps/anonymization/tests/test_tasks.py` — exit 0
- `uv run pytest apps/anonymization/tests/ apps/cases/tests/ apps/intake/tests/` — exit 0 (regressão cross-app)
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/anonymization/{tasks,signals,apps}.py
  - apps/anonymization/tests/test_tasks.py
  - config/settings/{base,prod,test}.py
  - docker-compose.yml
  - docker-compose.dev.yml
  - .env.example
  - docs/adr/ADR-0007-anonimacao-presidio-fail-closed.md
allowed_incidental_files: []
out_of_scope:
  - re-identificação/benchmark (005); qualquer mudança em apps/intake; cluster llm (06)
```

Escale ao parent se: o signal colidir com o das comunicações (ordem/efeitos); o worker exigir imagem própria além do documento.

## Critérios de aceitação

- [ ] R1–R7 comprovados pelos comandos da matriz (cenários fail-closed e assíncrono da spec)
- [ ] Falha de anonimização ⇒ FAILED + texto anonimizado permanece vazio
- [ ] Trigger cobre os 3 paths de entrada em ANONYMIZING sem tocar o intake
- [ ] Gate parcial do slice verde
