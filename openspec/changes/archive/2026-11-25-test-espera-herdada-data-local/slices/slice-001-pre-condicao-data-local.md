# Slice 001 — Pré-condição do teste de espera herdada compara data local

## Identity

- Slice: S1
- Change: `test-espera-herdada-data-local`
- Task: `tasks.md` → S1 — Pré-condição do teste de espera herdada compara data local

## Objective

Fazer `test_pending_card_uses_inherited_wait_in_html` parar de depender do
horário do relógio: a pré-condição compara a data local de `created_at` com a
data local de hoje.

## Read first

- `AGENTS.md`
- `openspec/changes/test-espera-herdada-data-local/proposal.md`
- `openspec/changes/test-espera-herdada-data-local/design.md` — D1/D2/D3
- `apps/doctor/tests/test_reappearance_wait.py` — linha 80 (pré-condição)
- `apps/cases/day_bounds.py` — convenção «data local, nunca data UTC»

## Requirements

- R1: a pré-condição do teste compara data local com data local
  (`timezone.localdate(incoming.created_at) == timezone.localdate()`); o corpo
  do teste (card e partial com «Aguardando há 5 dias») permanece intacto.
- R2: o teste passa em qualquer horário — verificado no relógio real e com
  `now` fixado antes e depois da fronteira UTC/local.
- Evidência: execução do teste focado (RED antes, GREEN depois) e da suíte
  completa.

## Expected blast radius

- Esperado: `apps/doctor/tests/test_reappearance_wait.py` (1 linha).
- Sensível/intocável: qualquer arquivo de produção, migrations, templates,
  outros testes.
- Expansão de blast radius exige justificativa no relatório.

## Failing-before plan

Failing-before evidence:
- targeted test: `uv run pytest apps/doctor/tests/test_reappearance_wait.py::test_pending_card_uses_inherited_wait_in_html`
- expected failure: `assert datetime.date(<amanhã UTC>) == datetime.date(<hoje local>)`
  — reproduzível no relógio real apenas entre 21:00 e 00:00 (-03); fora do
  intervalo, reproduzir com `django.utils.timezone.now` fixado após a
  fronteira UTC (plugin pytest descartável, fora do repositório).

## Implementation constraints

- Trocar somente a linha da pré-condição; não alterar asserções do corpo.
- Não tocar produção; não adicionar helpers ou dependências.
- Linha ≤ 100 (ruff format).

## Validation

Focused validation (worker):
- `uv run pytest apps/doctor/tests/test_reappearance_wait.py -x`
- `uv run pytest apps/doctor -x`

Project-required gates: `AGENTS.md` §2 (ruff, format, mypy, pytest).

## Acceptance criteria

- [ ] R1 pré-condição compara data local com data local; corpo intacto
- [ ] R2 verde no relógio real e com `now` fixado antes/depois da fronteira
- [ ] Failing-before evidence registrada
- [ ] Gates do projeto verdes

## Escalation conditions

Pare e retorne `BLOCKED_NEEDS_DECISION` se: o corpo do teste também depender do
horário após a correção; surgir necessidade de alterar produção; qualquer
expansão material de escopo.

## Evidence report

`/tmp/hmd-slice-test-espera-herdada-001-report.md` — status
(`READY_FOR_REVIEW`/`BLOCKED_NEEDS_DECISION`), requirements→evidence,
arquivos alterados, failing-before, passing-after, comandos+exit, riscos.

## Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy in `AGENTS.md` and preserve existing
conventions. Prefer the smallest correct change; avoid speculative abstraction
and unrelated refactoring.
Produce failing-before evidence, implement the smallest correct change, run
focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
