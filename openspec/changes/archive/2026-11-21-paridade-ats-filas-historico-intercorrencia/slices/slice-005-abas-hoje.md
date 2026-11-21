# Slice 005 — Abas «Hoje» (entregar por último)

## 1. Identity

- Slice: `slice-005-abas-hoje`
- Change: `paridade-ats-filas-historico-intercorrencia`
- Task: `tasks.md` § 5 — depende dos slices 003 e 004 (superfícies de arquivo e carimbos
  de agendamento existem).

## 2. Objective

Re-escopar as abas de trabalho para janelas do dia corrente por decisor: «Decididos Hoje»
do médico e «Processados Hoje» do agendador, com contadores, ordenação e títulos
atualizados — os casos antigos do agendador passam a viver no histórico; os do médico não
têm superfície (paridade ats-web, perda aprovada no design).

## 3. Read first

- `AGENTS.md`
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/proposal.md`
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/design.md` — D5 (par médico + backfill) / D7 (perda aprovada do médico)
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/specs/doctor-decision/spec.md` — MODIFIED «Fila médica operacional…»
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/specs/scheduling/spec.md` — MODIFIED «Fila do agendador completa»
- `apps/doctor/views.py` — `TAB_STATUSES`, fila/partial/contadores; serviço de decisão médica (carimbo no atomic)
- `apps/scheduler/views.py` — `TAB_STATUSES`, fila/partial/contadores (carimbos `scheduling_decided_*` do slice 004)
- `apps/cases/models.py` — `Case`, `CaseProcedure.doctor_decided_at`, `CaseEvent`
- helper de limites do dia local do projeto (padrão `local_day_bounds` do ats-web; verificar equivalente HMD)
- templates das filas e manual do usuário (`docs/`/`pwa-manual`)

## 4. Requirements

- R1: «Decididos Hoje» — casos com decisão médica registrada no dia corrente pelo próprio
  médico (`doctor_decided_by` + `doctor_decided_at` na janela do dia local),
  independentemente do status atual (inclui caso encerrado no mesmo dia); ordenação por
  data/hora da decisão desc; caso decidido em dia anterior não aparece em nenhuma aba;
  decisão legada com decisor nulo (backfill fail-closed) não aparece na aba de ninguém.
- R2: «Processados Hoje» — decisões de agendamento (confirmação OU negação) do dia
  corrente pelo próprio agendador (`scheduling_decided_by` + `scheduling_decided_at` do
  slice 004), qualquer status atual; ordenação por data/hora desc; caso de dia anterior
  fora das abas (consultável no histórico do slice 004).
- R3: carimbos médicos — `Case.doctor_decided_at`/`doctor_decided_by` gravados no MESMO
  atomic da decisão (fonte continua sendo a row por procedimento; teste anti-drift
  `Case.doctor_decided_at == Max(procedures__doctor_decided_at)`); migration com backfill
  auditável via `CaseEvent` (último `CASE_STATUS_DOCTOR_ACCEPTED|DENIED` → timestamp+ator;
  ator nulo/sem evento ⇒ campos nulos, fail-closed documentado).
- R4: contadores, partial 20 s, paginação, guards, busca client-side e estado vazio
  preservados com a nova semântica; títulos «Casos decididos hoje»/«Processados Hoje» e
  manual do usuário atualizados (incl. registro da perda aprovada da visão de casos
  antigos pelo médico).
- Evidência: testes das filas (`apps/doctor/tests/`, `apps/scheduler/tests/`) cobrindo
  R1/R2/R4; teste de migration/backfill e anti-drift para R3; decisão pré-deploy no mesmo
  dia visível ao decisor correto (R3, efeito do backfill com ator).

## 5. Expected blast radius

- Esperado: `apps/cases/models.py` (campos) + migration com backfill, serviço de decisão
  médica (carimbo), `apps/doctor/views.py`, `apps/scheduler/views.py`, templates das
  filas, manual do usuário, testes.
- Sensível (não mudar sem escalada): ordenação das abas pendentes (`days_on_screen` desc
  nulls-last FIFO), guards, reserva/lock, spec `pdf-original-viewer`, carimbos
  `scheduling_decided_*` (do 004 — apenas consumidos aqui).
- Comportamento anterior preservado: pendentes inalteradas em escopo e ordenação.

## 6. Failing-before plan

- Targeted tests: cenários «Decididos Hoje limita-se às decisões do dia corrente»,
  «inclui apenas as próprias decisões», «caso decidido hoje aparece mesmo já encerrado»,
  «Processados Hoje limita-se às decisões do dia corrente», «inclui apenas as próprias
  decisões» (negação incluída), contadores e títulos.
- Expected failure: filtros atuais por status listam o caso de ontem e não o caso
  encerrado hoje; negação fora da aba processados; testes falham — registrar antes de
  implementar.

## 7. Implementation constraints

- Janela do dia em timezone local (fonte única do helper; sem duplicação).
- Migration aditiva com backfill por eventos — casos sem decisão ficam nulos e válidos.
- Não alterar o escopo das pendentes nem a ordenação por `days_on_screen`.
- Este slice NÃO cria superfícies novas — só re-escopa abas existentes.

## 8. Validation

- Focused: `uv run pytest apps/doctor apps/scheduler -k "queue or tab or decided or processed"`
  (nomes conforme suíte) + teste de migration (após a migration, `pytest --create-db`
  uma vez — quirk do harness).
- Project gates: bateria completa de `AGENTS.md` § 2.
- Runtime: smoke manual das duas filas com dados seed (caso de ontem vs. hoje, decisão
  pré-deploy visível ao decisor), evidência registrada.

## 9. Acceptance criteria

- [ ] R1: aba médica com janela do dia por decisor, ordenação desc, caso de ontem fora,
      caso encerrado hoje dentro, decisor nulo fora (fail-closed).
- [ ] R2: aba do agendador análoga (confirmação E negação), caso de ontem fora.
- [ ] R3: carimbos no atomic da decisão + backfill com ator + anti-drift verde.
- [ ] R4: contadores/partial/paginação/guards/estado vazio verdes; títulos e manual
      atualizados (perda do médico registrada).
- [ ] Quality gate completo verde (incl. `pytest --create-db` após a migration).

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: o carimbo no `Case` revelar conflito com o fluxo de
decisão (ex.: decisão parcial por procedimento); se o backfill encontrar decisões médicas
sem evento correspondente (premissa D5 violada) ou atores divergentes não resolúveis pelo
"último evento"; se a mudança de título/contadores colidir com specs não previstas (ex.:
`ui-shell`).

## 11. Evidence report

`/tmp/hmd-slice-005-report.md` (estrutura canônica do skill).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
