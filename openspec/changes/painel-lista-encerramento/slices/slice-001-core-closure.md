# Slice 001 — Core do encerramento administrativo

## Contexto necessário

- FSM: `apps/cases/models.py` (`Case`, `CaseStatus` 17 estados, transições
  `@transition`, hooks `_fsm_*`, `_run_transition` gravando
  `CASE_STATUS_<target>` + evento de operação no atomic).
- Lock operacional: campos `locked_by/locked_at/locked_until/lock_token/
  lock_context/lock_role` do `Case`; helpers em `apps/cases/locks.py`
  (`_clear_lock_fields` privado — NÃO duplicar; criar função pública de
  força-release com evento).
- Minimização: `apps/cases/closure.py::_clean_acknowledged_clinical_data`
  (deleta `CaseDocument`/`CaseAttachment`, zera `extracted_text`,
  `anonymized_text`, `pseudonym_map`, `structured_data`, `summary_text`,
  `suggested_action`, `policy_result`) — reutilizar, não duplicar.
- Eventos: `apps/cases/events.py::CaseEventType` (TextChoices — valor novo
  NÃO exige migration); `CASE_LOCK_RELEASED` já existe.
- Notificações: `apps/accounts/models.py::NotificationType` (choices
  CONGELADAS na migration `0004` — valor novo EXIGE `AlterField`);
  `apps/accounts/notifications.py::create_milestone_notifications(event)`
  (signal `CaseEvent.post_save` com `created=True`; `get_or_create` com
  constraint único); teste-invariante dos tipos em
  `apps/accounts/tests/test_notifications_model.py` (~linhas 78-85) lista
  os 3 marcos e iguala `field.choices` — atualizar no MESMO slice.
- Tasks em voo: handlers de erro chamam `fail_processing` (só aceita
  PDF_EXTRACTING/ANONYMIZING/LLM_EXTRACTING/LLM_SUMMARIZING) em
  `apps/intake/tasks.py` (~123-140), `apps/anonymization/tasks.py`
  (~174-181), `apps/pipeline/orchestrator.py` (~184-189).
- Catálogo de motivos (constante em `apps/cases/services.py`):
  `processing_error/llm_failure/system_bug/stuck_lock/duplicate_reprocess/
  other` (labels pt-BR).
- Permissão: defesa em profundidade no service (`active_role in
  {"manager","admin"}`) — a rota chega no slice 002.

## Goal

Transição excepcional auditável → CLEANED com minimização de dados,
força-release de lock auditável, recusa fail-closed (lease viva, CLEANED,
validações), notificação ao criador (tipo novo + migration) e tolerância
das tasks a caso CLEANED em voo.

## Deliverables

### R1 — FSM + catálogo + locks (`apps/cases/models.py`, `apps/cases/services.py`, `apps/cases/locks.py`)

- `ADMINISTRATIVE_CLOSURE_REASONS` + `ADMINISTRATIVE_CLOSURE_REASON_CHOICES`.
- `Case.administratively_close(...)` com `source=` todos exceto `CLEANED`.
- `force_release_case_lock(case, *, reason)` público no `locks.py`: limpa
  os campos E grava `CASE_LOCK_RELEASED` com payload de força (single-writer
  preservado; sem duplicar `_clear_lock_fields` em hook de modelo).
- `administratively_close_case(*, case, user, active_role, reason_code,
  reason_text) -> Case`: validações (catálogo, texto, CLEANED, papel,
  **lease de worker viva → DomainError**); atomic na ordem do design D2 —
  snapshot do lock no payload → força-release → minimização (reuso da
  limpeza do ack) → transição + eventos (`CASE_STATUS_CLEANED` +
  `CASE_ADMINISTRATIVELY_CLOSED` com `{reason_code, reason_text, by, role,
  had_lock, previous_lock_context}`).

### R2 — Notificação (tipo novo + migration)

- `NotificationType.ADMINISTRATIVELY_CLOSED = "administratively_closed"`.
- Migration `apps/accounts/migrations/0005_*.py` (AlterField choices — sem
  SQL no Postgres; `makemigrations --check` limpo).
- Gatilho em `create_milestone_notifications` para
  `CASE_ADMINISTRATIVELY_CLOSED` → destinatário `case.created_by`; link
  pelo destino centralizado existente (nir → `intake:case_detail`).
- Amend do teste-invariante dos tipos (4 marcos agora).

### R3 — Guards das tasks em voo

- Nos handlers de erro das 3 tasks (`apps/intake/tasks.py`,
  `apps/anonymization/tasks.py`, `apps/pipeline/orchestrator.py`):
  `refresh_from_db` e, se `status == CLEANED`, retornar sem
  `fail_processing` (caso encerrado durante o voo).

### R4 — Testes (`apps/cases/tests/test_administrative_closure.py` novo)

- Encerra de TODOS os estados não-CLEANED (parametrizado; inclui FAILED e
  AWAITING_NIR_ACK) → CLEANED; **eventos: exatamente +2** (status +
  administrativo; +1 release extra quando havia lock).
- Payload completo (código/texto/autor/papel/had_lock/previous_lock_context).
- Minimização: documentos/anexos deletados, campos clínicos zerados (fixture
  com dados clínicos reais — não-vacuidade).
- Lock persistido é limpo + evento de release com payload de força; caso
  sem lock idempotente na limpeza.
- Rejeições: CLEANED, texto vazio, código fora do catálogo, papel
  nir/doctor/scheduler, **lease viva (worker em processamento) recusa e
  estado inalterado**; lease EXPIRADA permite (stuck_lock).
- Notificação criada (tipo novo, destinatário criador, link detalhe);
  invariante dos 4 tipos verdes.
- Guard de task: caso CLEANED durante o voo → handler de erro retorna sem
  exceção e sem evento de falha (uma task representativa de cada arquivo).

## Out of Scope

- Rota/UI (slice 002); painel; Meus casos; métrica; índices de busca.

## Expected files

- apps/cases/models.py
- apps/cases/events.py
- apps/cases/services.py
- apps/cases/locks.py
- apps/cases/tests/test_administrative_closure.py (novo)
- apps/accounts/models.py
- apps/accounts/notifications.py
- apps/accounts/migrations/0005_*.py (novo)
- apps/accounts/tests/test_notifications_model.py (amend do invariante)
- apps/intake/tasks.py (guard)
- apps/anonymization/tasks.py (guard)
- apps/pipeline/orchestrator.py (guard)
- allowed incidental: testes existentes das 3 tasks, se o guard exigir
  fixture mínima (declarar no Deviations)

## Verification (RED → GREEN, mesmo comando)

```bash
TEST_DB_PORT=55435 uv run pytest apps/cases apps/accounts apps/intake apps/anonymization apps/pipeline -q
uv run python manage.py makemigrations --check --dry-run
```

(rodar `pytest --create-db` uma vez se a migration nova reclamar de reuse-db)

## Acceptance criteria

- Paramétrico dos estados verde; eventos/payload/minimização/lock/
  notificação/recusas pinados; guards de task verdes; suíte completa verde;
  ruff/format/mypy; `makemigrations --check` limpo.

## Deviations / learnings

- (preenchido na execução)
