# Slice 001 — Sumário completo ao agendador

## 1. Identity

- Slice: `slice-001-sumario-completo-agendador`
- Change: `paridade-ats-filas-historico-intercorrencia`
- Task: `tasks.md` § 1

## 2. Objective

O detalhe do caso do agendador passa a exibir o sumário clínico completo re-identificado
na renderização, em vez de apenas a primeira linha (paridade ats-web, política de
visibilidade D1).

## 3. Read first

- `AGENTS.md`
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/proposal.md`
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/design.md` — D1
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/specs/scheduling/spec.md` — MODIFIED «Visão do agendador limitada ao necessário»
- `apps/scheduler/presenters.py` — `summary_line` (primeira linha via `reidentify_text`)
- `apps/scheduler/views.py` — `build_scheduler_case_detail_context`
- template do detalhe do agendador em `templates/scheduler/`
- `/projects/dev/ats-web/apps/scheduler/views.py` — `_get_diagnosis` (referência de paridade; sumário completo)

## 4. Requirements

- R1: o detalhe do agendador exibe o sumário completo (`summary_text` inteiro,
  multi-linha) re-identificado na renderização, preservando as linhas do texto.
- R2: nenhum outro artefato do pipeline passa a ser exibido (estrutura extraída, alertas
  da policy, sugestão continuam fora do detalhe do agendador).
- R3: os gates de PDF/texto extraído, guards de papel, referência humana e trilha de
  eventos permanecem exatamente como estão.
- Evidência: testes de renderização do detalhe do agendador (suíte existente em
  `apps/scheduler/tests/`) atualizados/estendidos para R1/R2; R3 verificado pela suíte
  existente passar sem edição.

## 5. Expected blast radius

- Esperado: `apps/scheduler/presenters.py`, template do detalhe do agendador, testes do
  scheduler referentes ao one-liner.
- Sensível (não mudar sem escalada): `apps/anonymization/reidentify.py`, presenters do
  médico/painel, `apps/doctor/*`, gate `PDF_ALLOWED_STATUSES`, guards.
- Comportamento anterior preservado: cards da fila (continuam com o one-liner —
  truncamento de card é apresentação de lista, não restrição do detalhe).

## 6. Failing-before plan

- Targeted tests: cenários «Agendador vê o sumário completo re-identificado» e «Agendador
  não vê artefatos clínicos além do permitido» do delta `scheduling`.
- Expected failure: o teste do sumário completo falha (só a primeira linha é renderizada
  hoje); registrar saída antes de implementar.

## 7. Implementation constraints

- Re-identificação exclusivamente na renderização — nada re-identificado é persistido
  nem enviado a LLM (invariante do projeto).
- Não introduzir mudança de modelo/dados neste slice.
- Não implementar busca histórica nem abas «Hoje» (slices 004/005).

## 8. Validation

- Focused: `uv run pytest apps/scheduler -k "detail or presenter"` (nomes conforme suíte).
- Project gates: bateria completa de `AGENTS.md` § 2.
- Runtime: não exigida (renderização coberta por teste de template).

## 9. Acceptance criteria

- [ ] R1: teste fixa sumário multi-linha completo, sem tokens, no detalhe do agendador.
- [ ] R2: teste de ausência de estrutura/policy/sugestão continua verde.
- [ ] R3: suíte do scheduler e do projeto verde sem editar testes de gate/guarda.
- [ ] Quality gate completo verde.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: a mudança exigir tocar reidentify/anonymization;
se algum teste de PHI/gate existente quebrar por razão não óbvia; se o template do detalhe
revelar dependência não mapeada do truncamento.

## 11. Evidence report

`/tmp/hmd-slice-001-report.md` com status, requirements→evidence, arquivos alterados,
failing-before, passing-after, comandos+exit, riscos.

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
