# Slice 006 — «Processados Hoje» da equipe de agendamento

## 1. Identity

- Slice: `slice-006-processados-hoje-equipe`
- Change: `visibilidade-por-equipe`
- Task: `tasks.md` S6

## 2. Objective

A aba «Processados Hoje» (e o badge do header) lista as decisões de agendamento
(confirmação ou negação) do dia corrente de **qualquer decisor**, com o decisor
visível no card.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/visibilidade-por-equipe/design.md` — D3, D6, D8
- `openspec/changes/visibilidade-por-equipe/specs/scheduling/spec.md` — MODIFIED «Fila do agendador completa»
- `apps/scheduler/views.py` — `queue`/`_build_queue_context` (aba processados :298-302), `_queue_item`
- `apps/accounts/context_processors.py` — `queue_counters` (parte scheduler :116-120)
- `templates/scheduler/` — card da aba processados
- Testes: `apps/scheduler/tests/test_processed_today.py`, `test_queue_parity.py`, `apps/accounts/tests/test_queue_counters.py`, `test_header_nav.py`

## 4. Requirements

- R1: queryset da aba = `scheduling_decided_by__isnull=False` + janela do dia
  local; ordenação `-scheduling_decided_at` preservada; decisões de qualquer
  decisor listadas, qualquer status atual do caso.
- R2: rows com decisor nulo (backfill fail-closed, migration 0013) continuam
  FORA da aba — consultáveis pelo Histórico.
- R3: decisor visível no card (nome legível) com
  `select_related("scheduling_decided_by")` — sem N+1.
- R4: badge do header (`queue_counters` scheduler) com a mesma regra — coerente
  com a aba.
- R5: pendentes (ordenação `days_on_screen`, filtro de procedimento, reserva),
  guards, contadores de abas, partial 20 s e Histórico inalterados.

## 5. Expected blast radius

- `apps/scheduler/views.py` (queryset da aba processados + `_queue_item`),
  `apps/accounts/context_processors.py`, `templates/scheduler/` (card
  processados), testes listados.
- **Não muda**: gate de PDF/texto (slice 007), serviços de agendamento,
  `historical_case_detail`, manual (slice 007 completa a seção do agendador).
- Comportamento anterior que permanece: rótulo «Recebido há…», badge de
  transporte, uid nunca exibido.

## 6. Failing-before plan

Failing-before evidence:
- targeted test invertido (`test_processed_today.py`): decisão de hoje por OUTRO
  agendador → espera listada com decisor visível; hoje não é listada
  (`scheduling_decided_by=user`).
- targeted test: row com `scheduling_decided_by=NULL` na janela → continua fora.
- targeted test (`test_queue_counters.py`): badge conta decisão de outro decisor.

## 7. Implementation constraints

- Trocar `scheduling_decided_by=user` por `scheduling_decided_by__isnull=False`
  — não apenas remover o filtro.
- Não antecipar o slice 007 (gate de PDF).
- Sem filtro "por decisor" na UI (Non-Goal).

## 8. Validation

**Focused validation** — `uv run pytest apps/scheduler/tests/test_processed_today.py
apps/scheduler/tests/test_queue_parity.py apps/accounts/tests/test_queue_counters.py
apps/accounts/tests/test_header_nav.py`

**Project-required gates** — `AGENTS.md` §2 ao concluir.

## 9. Acceptance criteria

- [ ] R1: qualquer decisor, qualquer status atual (testes)
- [ ] R2: decisor nulo fora da aba (teste)
- [ ] R3: decisor no card sem N+1 (teste)
- [ ] R4: badge coerente (teste)
- [ ] R5: pendentes/Histórico/guards preservados (testes verdes)
- [ ] Failing-before no relatório; gate §2 executado

## 10. Escalation conditions

`BLOCKED_NEEDS_DECISION` se: contadores do header revelarem dependência não
mapeada; contradição spec×código; necessidade de tocar o gate de PDF (é slice 007).

## 11. Evidence report

`/tmp/hmd-slice-vis-006-report.md` (mesmo formato do slice 001).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-vis-006-report.md`.
