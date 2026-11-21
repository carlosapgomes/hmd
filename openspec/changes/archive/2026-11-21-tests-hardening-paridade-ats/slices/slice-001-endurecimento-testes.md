# Slice 001 — Endurecimento de testes de paridade (P2s dos slices 004/005)

## 1. Identity

- Slice: `slice-001-endurecimento-testes`
- Change: `tests-hardening-paridade-ats`
- Task: `tasks.md` § 1

## 2. Objective

Quitar dois P2s deferidos da change `paridade-ats-filas-historico-intercorrencia` com
três testes de regressão que exercitam serviços reais — sem nenhuma mudança de produção.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/tests-hardening-paridade-ats/proposal.md` e `design.md` (D1–D3)
- Origem dos P2s (reviews): `/tmp/hmd-openspec-loops/hmd-casos-encerrados/paridade-ats-filas-historico-intercorrencia/slice-004-20261121T180922/slices/slice-004-historico-agendador/review-1.md` (P2) e `.../slice-005-20261121T191129/slices/slice-005-abas-hoje/review-2.md` (P2)
- `apps/scheduler/tests/test_historical_search.py` (teste de issue ativa fabricado a substituir/complementar; testes de PDF existentes)
- `apps/cases/closure.py` (`open_post_closure_issue` + elegibilidade), `apps/scheduler/services.py` (`confirm_case_scheduling`), `apps/intake` (padrão de ciência: `acknowledge_case_receipt`)
- `apps/cases/migrations/0014_doctor_decided_stamps.py` (função de backfill), `apps/doctor/tests/test_decided_today.py` (teste de backfill existente por chamada direta; aba), `apps/cases/day_bounds.py` (D3)
- `apps/doctor/views.py` (aba «Decididos Hoje»: `doctor_decided_by` + janela)

## 4. Requirements

- R1: teste do histórico do agendador pelo caminho REAL — caso levado a `CLEANED`
  (confirmação → resposta final → ciência) e reaberto via `open_post_closure_issue`;
  verifica presença no universo do histórico com issue ativa e carimbo
  `scheduling_decided_*` preservado (nada de `post_schedule_issue_status` setado à mão).
- R2: teste `CLEANED` + PDF + mesmo decisor na superfície histórica — cards de
  PDF/texto extraído ausentes no detalhe histórico e endpoint do PDF respondendo 404.
- R3: teste do fluxo de backfill — seed pré-deploy (decisão médica apenas em
  `CaseEvent` + `CaseProcedure`, carimbos nulos, instante derivado de `day_bounds`
  dentro do dia corrente) → execução da função de backfill da migration 0014 →
  carimbos preenchidos com o ator do evento → decisão listada na aba «Decididos Hoje»
  do decisor correto (e ausente na de outro médico).
- Evidência: os três testes verdes na suíte completa; failing-before N/A por D1
  (pin de regressão de comportamento aceito — documentar no relatório).

## 5. Expected blast radius

- Esperado: `apps/scheduler/tests/test_historical_search.py`,
  `apps/doctor/tests/test_decided_today.py`; helper de seed apenas se idiomático e
  justificado.
- Sensível (NÃO mudar sem escalada): qualquer arquivo de produção, migrations,
  templates, specs, `day_bounds.py`.
- Comportamento preservado: tudo — suíte existente inteira verde sem edição.

## 6. Failing-before plan

- N/A por design D1: testes pinam comportamento já aceito (guard 002, universo 004,
  backfill 005). Em vez de RED, o contrato exige exercício dos serviços/funções REAIS
  (R1–R3) — o relatório deve apontar, por teste, a linha onde o serviço real é chamado.

## 7. Implementation constraints

- ZERO mudança de produção/migration/template/spec.
- Instantes via `day_bounds` (D3) — nunca `now−Nh`.
- Sem dependências novas; sem `--create-db` extra (D2).
- Se um teste novo revelar um BUG real de produção (vermelho que não é de seed): PARE
  e reporte `BLOCKED_NEEDS_DECISION` — não conserte produção nesta change de testes.

## 8. Validation

- Focused: `uv run pytest apps/scheduler/tests/test_historical_search.py apps/doctor/tests/test_decided_today.py -q`
- Project gates: bateria completa de `AGENTS.md` §2.
- Runtime: não exigida.

## 9. Acceptance criteria

- [ ] R1: teste exercita `confirm_case_scheduling` + ciência + `open_post_closure_issue`; sem estado fabricado.
- [ ] R2: CLEANED+PDF+mesmo decisor → cards ausentes + 404 verificados.
- [ ] R3: seed → backfill (função da 0014) → carimbos com ator → listagem na aba do decisor correto (e fora da de outro).
- [ ] Nenhum arquivo de produção alterado (diff apenas testes ± helper de teste).
- [ ] Quality gate completo verde.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: qualquer teste novo falhar por comportamento real
(bug de produção); se o seed pré-deploy não for construível sem tocar produção; se a
verificação de fila exigir mudança em views.

## 11. Evidence report

`/tmp/hmd-tests-hardening-001-report.md` (estrutura canônica; por teste: serviço real
exercitado, linha da chamada; suíte/gate com exit codes).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce the required evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
