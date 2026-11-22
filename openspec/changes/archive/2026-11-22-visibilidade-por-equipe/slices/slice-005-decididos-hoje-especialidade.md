# Slice 005 — «Decididos Hoje» da especialidade (médico)

## 1. Identity

- Slice: `slice-005-decididos-hoje-especialidade`
- Change: `visibilidade-por-equipe`
- Task: `tasks.md` S5

## 2. Objective

A aba «Decididos Hoje» (e o badge do header) lista as decisões médicas do dia
corrente de **qualquer decisor** dentro do acesso por subtipo do visualizador,
com o decisor visível no card.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/visibilidade-por-equipe/design.md` — D3 (isnull=False + select_related), D4 (subtipo + queue_counters), D6, D8
- `openspec/changes/visibilidade-por-equipe/specs/doctor-decision/spec.md` — MODIFIED «Fila médica operacional com filtro por estado e subtipo»
- `apps/doctor/views.py` — `_decided_today_cases` (:242-246), `_build_queue_context`, `_queue_item`, TAB_STATUSES
- `apps/accounts/context_processors.py` — `queue_counters` (parte doctor, :95-107)
- `templates/doctor/` — card da aba decididos (onde renderizar o decisor)
- Testes: `apps/doctor/tests/test_decided_today.py`, `test_queue.py`, `apps/accounts/tests/test_queue_counters.py`, `apps/accounts/tests/test_manual.py`

## 4. Requirements

- R1: queryset da aba = `doctor_decided_by__isnull=False` + janela do dia local +
  **predicado de subtipo do visualizador preservado** (`_accessible_cases`);
  ordenação `-doctor_decided_at` preservada; decisões de qualquer decisor listadas.
- R2: rows com decisor nulo (backfill fail-closed, migrations 0014) continuam
  FORA da aba.
- R3: decisor visível no card (nome legível) com `select_related("doctor_decided_by")`
  — sem N+1.
- R4: badge do header (`queue_counters`) conta com a mesma regra
  (`doctor_decided_by__isnull=False` + matriz de subtipo) — coerente com a aba.
- R5: pendentes, guards, contadores de abas, paginação e partial 20 s inalterados;
  manual (seção Médico) atualizado («decididos hoje» da especialidade).

## 5. Expected blast radius

- `apps/doctor/views.py`, `apps/accounts/context_processors.py`,
  `templates/doctor/` (card decididos), `templates/accounts/manual.html` (seção
  Médico), testes listados.
- **Não muda**: `_accessible_cases`/`DoctorAccess`, fila de pendentes, decision
  flow, scheduler (slices 006/007), eventos.
- Comportamento anterior que permanece: dias anteriores fora das abas (sem
  histórico médico — perda aprovada), uid nunca exibido.

## 6. Failing-before plan

Failing-before evidence:
- targeted test invertido (`test_decided_today.py`): decisão de hoje por OUTRO
  médico em subtipo acessível → espera listada com decisor visível; hoje não é
  listada (`doctor_decided_by=user`).
- targeted test: row com `doctor_decided_by=NULL` dentro da janela → continua
  fora da aba (garante `isnull=False`).
- targeted test (`test_queue_counters.py`): badge conta decisão de outro decisor
  no subtipo do visualizador; hoje não conta.

## 7. Implementation constraints

- Trocar `doctor_decided_by=user` por `doctor_decided_by__isnull=False` — não
  apenas remover o filtro (P1-2 do review 2).
- Não criar filtro "por decisor" na UI (Non-Goal).
- Subtipo fora do acesso do visualizador NÃO listado (cenário novo do delta).

## 8. Validation

**Focused validation** — `uv run pytest apps/doctor/tests/test_decided_today.py
apps/doctor/tests/test_queue.py apps/accounts/tests/test_queue_counters.py
apps/accounts/tests/test_manual.py`

**Project-required gates** — `AGENTS.md` §2 ao concluir.

## 9. Acceptance criteria

- [ ] R1: qualquer decisor + subtipo do visualizador (testes invertido e negativo)
- [ ] R2: decisor nulo fora da aba (teste)
- [ ] R3: decisor no card sem N+1 (teste + `assertNumQueries` se já existir padrão)
- [ ] R4: badge coerente com a aba (teste)
- [ ] R5: pendentes/guards/manual (testes verdes + manual atualizado)
- [ ] Failing-before no relatório; gate §2 executado

## 10. Escalation conditions

`BLOCKED_NEEDS_DECISION` se: o predicado de subtipo não puder ser reaplicado à
aba; `queue_counters` revelar dependência não mapeada; contradição spec×código.

## 11. Evidence report

`/tmp/hmd-slice-vis-005-report.md` (mesmo formato do slice 001).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-vis-005-report.md`.
