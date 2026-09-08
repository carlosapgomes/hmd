# Slice 002: Intercorrência — transição `reopen_scheduling` (unidade 1)

## Objetivo

Nova transição FSM `reopen_scheduling` (`FINAL_REPLY_POSTED →
AWAITING_SCHEDULING` — segunda transição nova pós-change-03, prevista no
guardrail do design 03) + serviço `reopen_scheduling_after_incident`:
desmarcar por intercorrência um caso confirmado na **unidade 1**, com motivo
obrigatório, limpeza dos dados de agendamento, evento auditável e comunicação
ao NIR. Unidade 2: bloqueado com erro nomeado.

## Contexto necessário

- Slice 001 entregue: campos de agendamento + `apps/scheduler/services.py`
  (padrão de serviço transacional; constantes de texto).
- `apps/cases/models.py` — padrão de transição NOVA: ver
  `bypass_pipeline_divergence` (change 06, slice 004) como referência de
  `@transition` + operação pública `_run_transition` com payload extra.
- Design D2 (`openspec/changes/scheduler-multi-unit/design.md`) — regra da
  unidade; `_fsm_reopen_scheduling` sem estado novo.
- `post_user_communication` (change 03) para a mensagem ao NIR.

## Requisitos verificáveis

- **R1** Transição `reopen_scheduling` em `apps/cases/models.py`:
  `@transition(field="status", source=CaseStatus.FINAL_REPLY_POSTED,
  target=CaseStatus.AWAITING_SCHEDULING)` + operação pública com `*, user,
  role` (payload do evento `CASE_STATUS_AWAITING_SCHEDULING` ganha
  `source` real `FINAL_REPLY_POSTED` — o `_run_transition` já carrega — e
  `reason` via `extra_payload`); tabela de transições do design 03 não tem
  mais conflitos (nenhuma outra sai de `FINAL_REPLY_POSTED` além de
  `nir_acknowledge`).
- **R2** `reopen_scheduling_after_incident(case, *, reason, user, role)` em
  `apps/scheduler/services.py`: valida estado == `FINAL_REPLY_POSTED` (erro
  nomeado caso contrário), `scheduled_unit == 1` (**unidade 2 → erro nomeado
  "intercorrência desabilitada para unidade 2"**), motivo não vazio; no MESMO
  atomic: transição `reopen_scheduling`, limpa `scheduled_unit`/
  `scheduled_datetime`/`scheduled_location`/`scheduled_by`/
  `scheduled_decided_at` (null/blank), persiste `scheduling_reopen_reason`,
  posta comunicação ao NIR (constante `REPLY_REOPEN_TEMPLATE` interpolada com
  o motivo: retorno à fila para novo agendamento).
- **R3** Casos `SCHEDULING_CONFIRMED`/`AWAITING_NIR_ACK` (fora da janela)
  → erro nomeado sem efeito; motivo vazio → rejeitado sem escrita.
- **R4** Após reabertura, `confirm_case_scheduling` do slice 001 aceita o
  caso (fonte `AWAITING_SCHEDULING`) e produz NOVA resposta final — ciclo
  completo testável.
- **R5** Testes: unidade 1 feliz (estado, campos limpos — inclusive
  `scheduled_by` —, evento com `reason`, comunicação com motivo); unidade 2
  bloqueada (erro nomeado, caso inalterado); estado fora da janela ×2;
  motivo vazio; ciclo reabrir→reconfirmar com nova resposta.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/cases/models.py` | `test_reopen_transition_happy`, `test_reopen_wrong_source_blocked` |
| R2 | `apps/scheduler/services.py` | `test_reopen_unit_1_clears_fields_and_notifies`, `test_reopen_unit_2_blocked` |
| R3 | `apps/scheduler/services.py` | `test_reopen_out_of_window_*`, `test_reopen_empty_reason` |
| R4 | `apps/scheduler/services.py` | `test_reopen_then_reconfirm_cycle` |
| R5 | `apps/scheduler/tests/test_incident.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/models.py                # +transição reopen_scheduling (sem migration)
  - apps/scheduler/services.py          # +serviço +constante de texto
  - apps/scheduler/tests/test_incident.py

out_of_scope:
  - UI (slice 003); migration nova (nenhuma)
  - nir_acknowledge/AWAITING_NIR_ACK em diante (change 09)
  - mudanças em communications.py/events.py
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/scheduler/tests/test_incident.py`
- Falha esperada: `ImportError: cannot import name 'reopen_scheduling_after_incident'`
  (e `AttributeError: reopen_scheduling` no teste de transição).

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/scheduler/tests/ apps/cases/tests/`
  — exit 0 (transições do change 03 intactas).
- `uv run ruff check apps/scheduler apps/cases && uv run ruff format --check apps/scheduler apps/cases`
- `uv run mypy .`
- `uv run python manage.py makemigrations --check --dry-run` (sem migration nova)

## Critérios de aceitação

- [ ] R1–R5 comprovados; FSM fechada (nenhum estado novo; source único
      `FINAL_REPLY_POSTED`)
- [ ] Unidade 2 jamais reabre (erro nomeado, sem efeito)
- [ ] Ciclo reabrir→reconfirmar gera nova resposta final ao NIR
- [ ] Gate parcial do slice verde
