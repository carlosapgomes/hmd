# Slice 003 — Ações de escrita do NIR (gate + confirmação de divergência) em escopo de equipe

## 1. Identity

- Slice: `slice-003-gate-confirmacao-equipe`
- Change: `visibilidade-por-equipe`
- Task: `tasks.md` S3 (depende do slice 002)

## 2. Objective

Liberação/reenvio do gate e confirmação de procedimento por divergência operáveis
por **qualquer `nir`** — remoção da guarda por criador nos serviços, com re-check
de papel `nir` dentro da transação (defesa em profundidade) e tradução
`PermissionError → 403` nas views.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/visibilidade-por-equipe/design.md` — D1 (defesa em profundidade + PermissionError), D5, D6, D8
- `openspec/changes/visibilidade-por-equipe/specs/intake-nir/spec.md` — MODIFIED «Revisão NIR do gate», «Confirmação explícita do procedimento pelo NIR»
- `apps/intake/services.py` — `_assert_owned_by` (:422) e call sites em `release_retained_case` (:515), `confirm_case_procedure_review` (:648; re-check de papel modelo em :649-653), `resubmit_case_documents` (:772)
- `apps/intake/views.py` — `gate_release`, `gate_resubmit`, `procedure_review_confirm` (padrão `PermissionError`→`PermissionDenied` em :1324-1325)
- Testes: `apps/intake/tests/test_gate_actions.py`, `test_gate_divergence.py`, `test_procedure_review_confirmation.py`, `test_regulation_gate.py`, `test_intake_lock.py`

## 4. Requirements

- R1: `nir` não-criador consegue liberar (bypass) e reenviar documento no gate —
  evento com o **operador** como ator; validações de retenção vigente, 1 PDF e
  reprocessamento intactas.
- R2: `nir` não-criador consegue confirmar procedimento por divergência (manter/
  trocar + justificativa + leitura) — reprocessamento solicitado; digests `v1:`,
  `latest_review_event`, lock e erros 400/409 em corrida preservados.
- R3: defesa em profundidade — os 3 serviços trocam `_assert_owned_by` por
  re-check de papel `nir` **dentro da transação** (padrão já existente em
  `confirm_case_procedure_review`); papel revogado com sessão stalada
  (`active_role == "nir"` sem vínculo no banco) → `PermissionError`.
- R4: `gate_release` e `gate_resubmit` traduzem `PermissionError →
  PermissionDenied` (403), espelhando `procedure_review_confirm` — nunca 500.

## 5. Expected blast radius

- `apps/intake/services.py`, `apps/intake/views.py` (2 views de gate + docstring
  do `procedure_review_confirm` se citar criador), testes listados.
- **Não muda**: `acknowledge_case_receipt`/`case_ack` (slice 004), reenvio
  corrigido (`case_resubmit`), FSM, workers.
- Comportamento anterior que permanece: precedência de validações
  (estado/retenção antes do lock), mensagens nomeadas, eventos auditáveis.

## 6. Failing-before plan

Failing-before evidence:
- targeted test de **serviço** (novo em `test_regulation_gate.py` ou
  `test_gate_divergence.py`): `release_retained_case`/`confirm_case_procedure_review`
  chamados com `nir` não-criador → esperam sucesso; hoje falham com `Http404`
  (`_assert_owned_by`).
- targeted test de view: POST de `gate_release` com papel revogado (sessão
  stalada) → espera 403; hoje seria 500 (`PermissionError` não capturado).

## 7. Implementation constraints

- Re-check de papel consulta o vínculo real no banco (`user.roles.filter(...)`),
  não a sessão — padrão do serviço de confirmação.
- Sem mudar assinaturas públicas além do necessário; sem tocar `closure.py`.
- Não antecipar o slice 004.

## 8. Validation

**Focused validation** — `uv run pytest apps/intake/tests/test_gate_actions.py
apps/intake/tests/test_gate_divergence.py apps/intake/tests/test_procedure_review_confirmation.py
apps/intake/tests/test_regulation_gate.py apps/intake/tests/test_intake_lock.py`

**Project-required gates** — `AGENTS.md` §2 ao concluir.

## 9. Acceptance criteria

- [ ] R1: gate por não-criador OK, ator correto nos eventos (testes de serviço E view)
- [ ] R2: confirmação por não-criador OK; corridas 400/409 preservadas (testes)
- [ ] R3: re-check de papel nos 3 serviços, dentro da transação (testes diretos)
- [ ] R4: 403 (não 500) nas 2 views de gate com papel revogado (testes)
- [ ] Failing-before no relatório; gate §2 executado

## 10. Escalation conditions

`BLOCKED_NEEDS_DECISION` se: a ordem de validações dos serviços não puder ser
preservada; o re-check de papel revelar incompatibilidade com o modelo de
multi-role; contradição spec×código.

## 11. Evidence report

`/tmp/hmd-slice-vis-003-report.md` (mesmo formato do slice 001).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-vis-003-report.md`.
