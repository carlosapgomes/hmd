# Slice 001 — Convergência do re-check de papel e limpeza de parâmetro morto

## 1. Identity

- Slice: `slice-001-convergencia-recheck-limpeza`
- Change: `p2-hardening-visibilidade-equipe`
- Task: `tasks.md` S1

## 2. Objective

Quitar os itens de higiene de código (5 e 1 da change) sem nenhuma mudança de comportamento: helper `_assert_nir_role` com mensagem por ação + convergência do re-check inline; remoção do parâmetro `user` morto.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/p2-hardening-visibilidade-equipe/proposal.md` e `design.md` — D1
- `apps/intake/services.py` — `_assert_nir_role` (:422-435) e call sites (`release_retained_case`, `resubmit_case_documents`); re-check inline em `confirm_case_procedure_review` (:648-654)
- `apps/doctor/views.py` — `_decided_today_cases` (:225-230, parâmetro `user` sem uso) e call sites
- Testes: `apps/intake/tests/test_gate_actions.py`, `test_gate_divergence.py`, `test_procedure_review_confirmation.py`, `test_regulation_gate.py`, `apps/doctor/tests/test_decided_today.py`, `test_queue.py`

## 4. Requirements

- R1: `_assert_nir_role` aceita contexto de ação (keyword-only) e os 4 pontos (3 call sites + re-check inline da confirmação) convergem para ele — mensagens de `PermissionError` permanecem SEMANTICAMENTE idênticas por ação (paridade de string exigida: nenhum teste existente de mensagem pode quebrar).
- R2: `_decided_today_cases` perde o parâmetro `user`; call sites ajustados; nenhum outro comportamento muda.
- R3: suíte verde contínua (nenhum teste existente editado neste slice — paridade é a prova).

## 5. Expected blast radius

- `apps/intake/services.py`, `apps/doctor/views.py`, testes listados (só se paridade exigir — idealmente zero edições de teste).
- **Não muda**: views, mensagens visíveis, FSM, specs, templates.

## 6. Failing-before plan

Failing-before evidence (refactor sem seam RED de comportamento — evidência de paridade):
- Antes: rodar `apps/intake/tests/test_gate_actions.py apps/intake/tests/test_procedure_review_confirmation.py apps/doctor/tests/test_decided_today.py` (verde) e capturar as strings das mensagens de PermissionError assertadas (log `/tmp/hmd-p2h-001-before.log`).
- Depois: mesmos testes verdes SEM edição (log `/tmp/hmd-p2h-001-after.log`) — a paridade intacta é a evidência de que o refactor não mudou comportamento.

## 7. Implementation constraints

- Proibido alterar asserções de teste para acomodar o refactor (paridade é o contrato).
- Sem mudança de assinatura pública além do helper e do `_decided_today_cases`.

## 8. Validation

**Focused** — `uv run pytest apps/intake/tests/test_gate_actions.py apps/intake/tests/test_gate_divergence.py apps/intake/tests/test_procedure_review_confirmation.py apps/intake/tests/test_regulation_gate.py apps/doctor/tests/test_decided_today.py apps/doctor/tests/test_queue.py -q`
**Gates** — `AGENTS.md` §2 completos ao concluir.

## 9. Acceptance criteria

- [ ] R1: 4 pontos no helper; mensagens idênticas; zero teste editado (git diff de testes vazio neste slice)
- [ ] R2: parâmetro removido; call sites ajustados
- [ ] R3: focused verde + gate §2; logs before/after no relatório

## 10. Escalation conditions

`BLOCKED_NEEDS_DECISION` se a convergência exigir mudar qualquer mensagem assertada por teste (sinal de que a paridade não é alcançável sem decisão).

## 11. Evidence report

`/tmp/hmd-p2h-001-report.md` (formato da casa).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce the before/after parity evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-p2h-001-report.md`.
