# Slice 004 — Histórico do agendador e carimbos de decisão de agendamento

## 1. Identity

- Slice: `slice-004-historico-agendador`
- Change: `paridade-ats-filas-historico-intercorrencia`
- Task: `tasks.md` § 4 — depende do slice 002 (issue ativa no universo da busca).

## 2. Objective

Carimbos persistentes de decisão de agendamento (confirmação E negação, com decisor) +
página de histórico do agendador com busca por nº de ocorrência ou nome do paciente sobre
casos que passaram por decisão de agendamento (incluindo encerrados e com intercorrência
ativa, qualquer decisor), com acesso de detalhe em leitura.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/proposal.md`
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/design.md` — D5 (par de agendamento) / D6
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/specs/scheduling/spec.md` — ADDED «Histórico do agendador»
- `apps/scheduler/views.py` — `TAB_STATUSES`, `PDF_ALLOWED_STATUSES`, detalhe vigente
- `apps/scheduler/services.py` — `confirm_case_scheduling` (carimba `scheduled_*`),
  `deny_case_scheduling` (hoje NÃO carimba decisor/timestamp), `reopen_scheduling_after_incident`
- `apps/cases/models.py` — `Case`, campos de agendamento, `CaseEvent`
- `apps/scheduler/urls.py`, `templates/scheduler/`
- `/projects/dev/ats-web/apps/scheduler/views.py` — `scheduler_historical_search`/`_scheduler_historical_queryset` (referência de paridade)

## 4. Requirements

- R1: carimbos — `Case.scheduling_decided_at`/`scheduling_decided_by` gravados no MESMO
  atomic por `confirm_case_scheduling` E `deny_case_scheduling` (negação passa a carimbar
  ambos), atualizados em re-decisões, NUNCA zerados pela reabertura (os 5 campos do
  agendamento corrente continuam sendo limpos normalmente). Migration com backfill
  auditável via `CaseEvent`: último `CASE_STATUS_SCHEDULING_CONFIRMED|DENIED` por caso →
  timestamp+ator; sem evento/ator nulo → campos nulos (fail-closed; caso fica fora das
  abas «Hoje» de todos, sem afetar o histórico).
- R2: página de histórico (papel `scheduler`/`admin`) — universo `scheduling_decided_at`
  preenchido (inclui `CLEANED` e issue ativa, qualquer decisor; **exclui** negativa médica
  encerrada — nunca teve decisão de agendamento); termo casa nº OU `patient_name`; cap 50;
  estado vazio sem critério; referência humana.
- R3: resultados exibem status legível e dados do agendamento anterior; link para o
  detalhe em escopo histórico (leitura, sem ações de confirmação/negação/desmarcação).
- R4: PDF/texto extraído no detalhe histórico seguem o gate vigente (status permitidos +
  `scheduled_by` do próprio agendador; `CLEANED` fora).
- R5: papel não-agendador → 403.
- Evidência: testes em `apps/scheduler/tests/` cobrindo R1 (carimbos, backfill, casos
  positivos/negativos: confirmação nova, negação nova e legada, reaberto pré-ciência,
  negativa médica encerrada) e R2–R5.

## 5. Expected blast radius

- Esperado: `apps/cases/models.py` (campos) + migration com backfill,
  `apps/scheduler/services.py` (carimbo na negação e na confirmação — reutilizando o
  atomic existente), `apps/scheduler/views.py`, `apps/scheduler/urls.py`,
  `templates/scheduler/historical_search.html` (nome final conforme convenção), ajuste do
  detail para modo leitura quando acessado do histórico, testes.
- Sensível (não mudar sem escalada): `TAB_STATUSES` vigentes (só o slice 005 os toca),
  `reopen_scheduling_after_incident` (limpeza dos 5 campos permanece), gate
  `PDF_ALLOWED_STATUSES`.
- Comportamento anterior preservado: fila (abas, partial, contadores) intocada neste
  slice; carimbo não altera o detail do agendamento corrente.

## 6. Failing-before plan

- Targeted tests: cenários do delta `scheduling` (busca por nome de caso antigo encerrado,
  busca por nº de outro decisor, negativa médica encerrada fora do histórico, estado vazio
  sem critério, detalhe histórico sem ações com gate de PDF, 403 não-agendador) + R1
  (negação nova carimba decisor+timestamp; caso negado legado aparece no histórico via
  backfill; reaberto pré-ciência mantém carimbo).
- Expected failure: 404 de rota (página não existe); carimbo inexistente (negação sem
  decisor hoje); registrar antes de implementar.

## 7. Implementation constraints

- Leitura pura na superfície de histórico — nenhuma escrita (carimbos são efeito dos
  serviços de decisão existentes, não da página).
- Não estreitar as abas da fila (slice 005).
- Universo por campo persistente (D5/D6) — sem varredura de eventos na consulta da página.
- Carimbos médicos (`doctor_decided_*`) NÃO fazem parte deste slice (005).

## 8. Validation

- Focused: `uv run pytest apps/scheduler -k "historical or decided"` (nomes conforme
  suíte) + teste de migration (após a migration, `pytest --create-db` uma vez — quirk do
  harness).
- Project gates: bateria completa de `AGENTS.md` § 2.
- Runtime: não exigida (coberta por teste de template).

## 9. Acceptance criteria

- [ ] R1: confirmação e negação carimbam os dois campos no atomic; backfill cobre legados
      por eventos; fail-closed para ator nulo testado; reabertura não zera carimbo.
- [ ] R2: universo completo (confirmações, negações, reabertos, CLEANED, issue ativa,
      qualquer decisor; negativa médica encerrada excluída), cap 50, estado vazio,
      referência humana.
- [ ] R3: detalhe em leitura sem ações de agendamento.
- [ ] R4: gate de PDF/texto preservado no acesso histórico.
- [ ] R5: 403 para não-agendador.
- [ ] Quality gate completo verde.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: o backfill por eventos encontrar decisões de
agendamento sem evento correspondente (premissa D5 violada); se o detalhe exigir mudança
nos gates de PDF; se o carimbo na negação colidir com gates que usam `scheduled_by`
(verificar `PDF_ALLOWED_STATUSES` + `_get_scheduler_processed_case_or_404`).

## 11. Evidence report

`/tmp/hmd-slice-004-report.md` (estrutura canônica do skill).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
