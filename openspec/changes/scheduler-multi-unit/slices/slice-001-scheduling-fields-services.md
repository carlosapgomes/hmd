# Slice 001: Campos de agendamento + serviços confirmar/negar

## Objetivo

`Case` ganha os campos de agendamento (migration única) e os serviços
transacionais de **confirmar** (unidade 1|2 → resposta final ao NIR por
unidade) e **negar** (motivo obrigatório), encadeando as transições FSM até
`FINAL_REPLY_POSTED` no mesmo atomic e postando a resposta na thread de
comunicações do caso.

## Contexto necessário

- `apps/cases/models.py` — padrão de transição pública (`_run_transition` com
  `*, user, role`; operações existentes `await_scheduling_confirmation`,
  `confirm_scheduling`, `deny_scheduling`, `post_final_reply` — change 03);
  migrations existentes até `0007_llm_artifacts` (próxima = **0008**).
- `apps/cases/communications.py::post_user_communication(case, *, user, role,
  body)` — posta user message na thread (NIR já a vê no detalhe do intake).
- `apps/cases/events.py` — eventos `CASE_STATUS_*` (nenhum novo necessário).
- Padrão de serviço transacional com `select_for_update`:
  `apps/cases/procedures.py::record_doctor_procedure_decisions` (atomic +
  transições encadeadas + eventos juntos).
- Design D1/D2 (`openspec/changes/scheduler-multi-unit/design.md`) — campos e
  textos de resposta como **constantes do módulo** (dados, não strings
  espalhadas); unidade 2 usa o texto EXATO do plano §4.
- `django.utils.timezone` para data/hora aware; validação "não no passado".

## Requisitos verificáveis

- **R1** Migration única `apps/cases/migrations/0008_case_scheduling.py` com
  os 7 campos de D1 (todos null/blank; `scheduled_unit` choices 1|2; FK
  `scheduled_by` `SET_NULL` related_name `cases_scheduled`); sem drift
  (`makemigrations --check`).
- **R2** `confirm_case_scheduling(case, *, unit, scheduled_datetime,
  scheduled_location, user, role)` em `apps/scheduler/services.py`: valida
  estado ∈ {`SCHEDULER_REQUESTED`, `AWAITING_SCHEDULING`}, `unit ∈ {1,2}`,
  data/hora aware não no passado; no MESMO atomic: encadeia
  `await_scheduling_confirmation` → `confirm_scheduling` → `post_final_reply`
  (3 eventos `CASE_STATUS_*`), persiste os 5 campos (`scheduled_unit`,
  `scheduled_datetime`, `scheduled_location`, `scheduled_by=user`,
  `scheduled_decided_at=now`) e posta `post_user_communication` com o texto
  por unidade (constantes `REPLY_UNIT_1_TEMPLATE`/`REPLY_UNIT_2_TEXT` —
  unidade 1 interpolada com local + data/hora local formatada;
  **unidade 2 é o texto exato do plano**).
- **R3** Validações de R2 levantam erros nomeados (`ValueError` com motivo
  específico: estado, unidade, data no passado) **antes** de qualquer
  escrita; nada persistido em falha (rollback).
- **R4** `deny_case_scheduling(case, *, reason, user, role)`: motivo não
  vazio (strip); encadeia `await` → `deny_scheduling` → `post_final_reply`
  + persiste `scheduling_denial_reason` + posta resposta final ao NIR com o
  motivo; mesmo regime de erros de R3.
- **R5** Concorrência/estado errado (`TransitionNotAllowed` do
  encadeamento): nenhuma escrita parcial (atomic); erro propaga tipado para
  a view tratar (slice 003) — nada de 500 silencioso no serviço.
- **R6** Testes: confirmar unidade 1 (estado final `FINAL_REPLY_POSTED`,
  campos persistidos, 3+1 eventos na ordem, comunicação com data/local);
  confirmar unidade 2 (texto exato); data no passado rejeitada sem escrita;
  negar com motivo (comunicação contém o motivo); negar sem motivo rejeitado;
  estado inválido sem efeito; confirmação a partir de `AWAITING_SCHEDULING`
  (reabertura futura do slice 002) funciona.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/cases/{models,migrations/0008_case_scheduling}.py` | `test_migration_fields`, `makemigrations --check` |
| R2/R3 | `apps/scheduler/services.py` (novo) | `test_confirm_unit_1_*`, `test_confirm_unit_2_exact_text`, `test_confirm_past_datetime_rejected` |
| R4 | `apps/scheduler/services.py` | `test_deny_publishes_reason`, `test_deny_empty_reason_rejected` |
| R5 | `apps/scheduler/services.py` | `test_wrong_state_no_partial_write` |
| R6 | `apps/scheduler/tests/test_services.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/models.py                      # +campos D1 (mínimo)
  - apps/cases/migrations/0008_case_scheduling.py
  - apps/scheduler/__init__.py
  - apps/scheduler/services.py                # constantes + 2 serviços
  - apps/scheduler/tests/__init__.py
  - apps/scheduler/tests/test_services.py
  - config/settings/base.py                   # INSTALLED_APPS += apps.scheduler

out_of_scope:
  - transição reopen/intercorrência (slice 002)
  - UI/fila/forms/nav (slice 003)
  - apps/intake; communications.py; events.py; signals
  - nir_acknowledge em diante (change 09)
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/scheduler/tests/test_services.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.scheduler'`.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/scheduler/tests/ apps/cases/tests/`
  — exit 0 (regressão do domínio FSM/eventos).
- `uv run ruff check apps/scheduler apps/cases && uv run ruff format --check apps/scheduler apps/cases`
- `uv run mypy .`
- `uv run python manage.py makemigrations --check --dry-run`

## Critérios de aceitação

- [ ] R1–R6 comprovados; texto da unidade 2 é o EXATO do plano §4
- [ ] Encadeamento das 3 transições + persistência + comunicação no MESMO
      atomic (assert de ordem/conteúdo dos eventos)
- [ ] Falha de validação nunca deixa escrita parcial
- [ ] Gate parcial do slice verde
