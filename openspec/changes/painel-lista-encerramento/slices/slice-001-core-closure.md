# Slice 001 — Core do encerramento administrativo

## Contexto necessário

- FSM: `apps/cases/models.py` (`Case`, `CaseStatus` 17 estados; padrão do
  repo: hook PRIVADO `_fsm_*` decorado `@transition` + operação PÚBLICA que
  chama `_run_transition` gravando `CASE_STATUS_<target>` + evento da
  operação no atomic; NÃO existe `apps/cases/services.py`).
- Serviços de fechamento: `apps/cases/closure.py` (dono do domínio —
  `acknowledge_case_receipt` com o padrão de minimização: coletar
  `file.name` de documentos/anexos ANTES do delete, chamar
  `_clean_acknowledged_clinical_data` (rows: deleta `CaseDocument`/
  `CaseAttachment`, zera `extracted_text/anonymized_text/pseudonym_map/
  structured_data/summary_text/suggested_action/policy_result`) e
  `transaction.on_commit(_delete_files_best_effort)` para os ARQUIVOS).
- Lock: campos `locked_by/locked_at/locked_until/lock_token/lock_context/
  lock_role`; `apps/cases/locks.py` — `release_case_lock` re-lê sob
  `select_for_update` e MUTA a instância; lease comparada com
  `timezone.now()` aware; `CASE_LOCK_RELEASED` já existe em `events.py`.
- Notificações: `apps/accounts/models.py::NotificationType` (choices
  congeladas na migration `0004`; max_length 30); `create_milestone_
  notifications(event)` (signal `CaseEvent.post_save` `created=True`;
  `get_or_create` com constraint único; destino centralizado por papel —
  nir → `intake:case_detail`); teste-invariante dos tipos em
  `apps/accounts/tests/test_notifications_model.py` (~78-85); manual do
  usuário enumera marcos em `templates/accounts/manual.html` (~216-226).
- Pipeline em voo: atomics de escrita clínica SEM checagem de status em
  `apps/pipeline/policy.py` (~679-683: `policy_result`),
  `apps/pipeline/llm2_service.py` (~428-432: `summary_text`/
  `suggested_action`), `apps/pipeline/prior_case.py` (~218-219); chamadores
  com `refresh_from_db` pré-passo em `apps/pipeline/orchestrator.py`
  (~132/157/163), `apps/intake/tasks.py` (~127-128, refresh no except),
  `apps/anonymization/tasks.py` (~180-181). Contextos de worker começam
  com `worker_`; handlers de erro chamam `fail_processing` (só aceita
  PDF_EXTRACTING/ANONYMIZING/LLM_EXTRACTING/LLM_SUMMARIZING).
- Exceção do repo para validação: `ValueError` nomeada (NÃO existe
  `DomainError`).

## Goal

Transição excepcional auditável → CLEANED com minimização completa (rows +
arquivos físicos), força-release de lock auditável, recusa fail-closed,
notificação ao criador (tipo novo + migration + spec notifications) e
abort de escrita pós-encerramento nos workers.

## Deliverables

### R1 — FSM + catálogo + locks + service (`apps/cases/models.py`, `apps/cases/closure.py`, `apps/cases/locks.py`, `apps/cases/events.py`)

- `ADMINISTRATIVE_CLOSURE_REASONS` + `_REASON_CHOICES` em `closure.py`.
- Hook PRIVADO `_fsm_administratively_close` (`source=` todos exceto
  `CLEANED`) + op PÚBLICA `Case.administratively_close(*, user, role,
  reason_code, reason_text)` via `_run_transition`, gravando
  `CASE_ADMINISTRATIVELY_CLOSED` com payload `{reason_code, reason_text,
  by, role, had_lock, previous_lock_context, previous_lock_until}`.
- `force_release_case_lock(case, *, reason)` público no `locks.py`:
  espelha `release_case_lock` (select_for_update + MUTA a mesma instância)
  e grava `CASE_LOCK_RELEASED` com payload de força.
- `administratively_close_case(*, case, user, active_role, reason_code,
  reason_text) -> Case` em `closure.py`: validações (catálogo, texto,
  CLEANED, papel, **lease viva → `ValueError`** com
  `timezone.now()` aware e prefixo `worker_`); atomic na ordem: snapshot
  do lock → force-release → coletar `file.name` → minimização (helper do
  MESMO módulo) → op pública → `on_commit(_delete_files_best_effort)`.

### R2 — Notificação (tipo novo + migration + manual)

- `NotificationType.ADMINISTRATIVELY_CLOSED = "administratively_closed"`.
- Migration `apps/accounts/migrations/0005_*.py` (AlterField choices — sem
  DDL no Postgres; `makemigrations --check` limpo).
- Gatilho em `create_milestone_notifications`: título e preview FIXOS
  "Caso encerrado administrativamente" (sem `reason_text`, sem PHI);
  destinatário `case.created_by`.
- Amend do teste-invariante (4 marcos) + docstring do módulo (deixa de
  dizer "conjunto FECHADO") + `templates/accounts/manual.html` (enumera o
  4º marco).

### R3 — Abort de escrita pós-encerramento (workers)

- Nas 3 tasks/orchestrator (`apps/intake/tasks.py`,
  `apps/anonymization/tasks.py`, `apps/pipeline/orchestrator`): após cada
  `refresh_from_db` pré-passo e no `except`, retornar sem persistir quando
  `status == CLEANED` (handler de erro NÃO chama `fail_processing`).
- Nos atomics de escrita clínica (`apps/pipeline/policy.py`,
  `apps/pipeline/llm2_service.py`, `apps/pipeline/prior_case.py`):
  re-ler o caso dentro do atomic e retornar sem persistir quando
  `CLEANED`.

### R4 — Testes (`apps/cases/tests/test_administrative_closure.py` novo)

- Encerra de TODOS os estados não-CLEANED (parametrizado; inclui FAILED e
  AWAITING_NIR_ACK) → CLEANED; **eventos: exatamente +2** (+1 release
  quando havia lock) verificados com `refresh_from_db`.
- Payload completo (código/texto/autor/papel/had_lock/previous_lock_*).
- Minimização: rows deletadas, campos zerados E **arquivo físico sumido do
  storage** (fixture com upload real em storage de teste); download de
  documento do caso encerrado não é mais possível.
- Lock persistido é limpo + evento de release com payload de força;
  **instância pós-operação sem lock (refresh_from_db)** — pina que o save
  da transição não ressuscita o lock; caso sem lock idempotente.
- Rejeições: CLEANED, texto vazio, código fora do catálogo, papel
  nir/doctor/scheduler, **lease viva recusa e estado inalterado**; lease
  EXPIRADA permite (stuck_lock).
- Notificação: tipo novo, destinatário criador, título/preview fixos SEM
  motivo; invariante dos 4 tipos verde; manual atualizado (assert do texto
  do 4º marco no HTML).
- Worker-zumbi: lease expirada → encerra → passo de escrita do pipeline
  (policy E llm2) roda → retorna sem persistir, campos seguem zerados,
  sem evento de falha (uma task representativa de cada arquivo + os 2
  serviços de pipeline).

## Out of Scope

- Rota/UI (slice 002); painel; Meus casos; métrica; índices de busca.

## Expected files

- apps/cases/closure.py
- apps/cases/models.py
- apps/cases/events.py
- apps/cases/locks.py
- apps/cases/tests/test_administrative_closure.py (novo)
- apps/accounts/models.py
- apps/accounts/notifications.py
- apps/accounts/migrations/0005_*.py (novo)
- apps/accounts/tests/test_notifications_model.py (amend do invariante)
- templates/accounts/manual.html (4º marco)
- apps/intake/tasks.py (abort)
- apps/anonymization/tasks.py (abort)
- apps/pipeline/orchestrator.py (abort)
- apps/pipeline/policy.py (gate no atomic)
- apps/pipeline/llm2_service.py (gate no atomic)
- apps/pipeline/prior_case.py (gate no atomic)
- allowed incidental: testes existentes das tasks/pipeline se o gate exigir
  fixture mínima (declarar no Deviations)

## Verification (RED → GREEN, mesmo comando)

```bash
TEST_DB_PORT=55435 uv run pytest apps/cases apps/accounts apps/intake apps/anonymization apps/pipeline -q
uv run python manage.py makemigrations --check --dry-run
```

(rodar `pytest --create-db` uma vez se a migration nova reclamar de reuse-db)

## Acceptance criteria

- Paramétrico dos estados verde; eventos/payload/minimização-com-arquivo/
  lock-sem-ressurreição/notificação fixa/recusas/aborts pinados; suíte
  completa verde; ruff/format/mypy; `makemigrations --check` limpo.

## Deviations / learnings

- (preenchido na execução)
