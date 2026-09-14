# Slice 001 — Core do encerramento administrativo

## Contexto necessário

- FSM: `apps/cases/models.py` (`Case`, `CaseStatus` 17 estados, transições
  `@transition`); hook de limpeza no mesmo atomic segue o padrão dos hooks
  existentes (`_fsm_*`).
- Lock operacional: campos `locked_by/locked_at/locked_until/lock_token/
  lock_context/lock_role` (`Case`).
- Eventos: `apps/cases/events.py::CaseEventType` (TextChoices — valor novo
  NÃO exige migration); gravação via o padrão dos serviços existentes
  (`apps/cases/services.py`).
- Notificações por marco: `apps/accounts/notifications.py::
  create_milestone_notifications(event)` — gatilho por tipo de evento.
- Catálogo de motivos (constante em `apps/cases/services.py`):
  `processing_error/llm_failure/system_bug/stuck_lock/duplicate_reprocess/
  other` (labels pt-BR).
- Permissão: defesa em profundidade no service (`active_role in
  {"manager","admin"}`) — a rota chega no slice 002.

## Goal

Transição excepcional auditável → CLEANED + limpeza de lock + notificação ao
criador, com validações fail-closed.

## Deliverables

### R1 — FSM + catálogo (`apps/cases/models.py`, `apps/cases/services.py`)

- `ADMINISTRATIVE_CLOSURE_REASONS` + `ADMINISTRATIVE_CLOSURE_REASON_CHOICES`.
- `Case.administratively_close(...)` com `source=` todos exceto `CLEANED`,
  hook limpando o lock no mesmo atomic.
- `administratively_close_case(*, case, user, active_role, reason_code,
  reason_text) -> Case` (validações D2; recarrega e devolve).

### R2 — Evento + notificação

- `CaseEventType.CASE_ADMINISTRATIVELY_CLOSED`.
- Evento com payload `{reason_code, reason_text, by: username, role}`.
- Gatilho no `create_milestone_notifications` → notificação ao criador com
  link para `intake:my_cases`.

### R3 — Testes (`apps/cases/tests/test_administrative_closure.py` novo)

- Encerra de TODOS os estados não-CLEANED (parametrizado; inclui FAILED e
  AWAITING_NIR_ACK) → CLEANED + evento + payload.
- Lock persistido é limpo; caso sem lock idempotente na limpeza.
- Rejeições: CLEANED, texto vazio, código fora do catálogo, papel
  nir/doctor/scheduler (service raises).
- Notificação criada (marco, destinatário criador).
- Regressão: suíte cases/accounts verde sem edição.

## Out of Scope

- Rota/UI (slice 002); painel; Meus casos; migrations (não há).

## Expected files

- apps/cases/models.py
- apps/cases/events.py
- apps/cases/services.py
- apps/accounts/notifications.py
- apps/cases/tests/test_administrative_closure.py (novo)
- allowed incidental: NENHUM

## Verification (RED → GREEN, mesmo comando)

```bash
TEST_DB_PORT=55435 uv run pytest apps/cases -q
```

## Acceptance criteria

- Paramétrico dos estados verde; evento/payload/lock/notificação pinados;
  rejeições fail-closed; suíte completa verde.

## Deviations / learnings

- (preenchido na execução)
