# Slice 004 — Ciência do NIR e reenvios corrigidos em escopo de equipe

## 1. Identity

- Slice: `slice-004-ciencia-reenvios-equipe`
- Change: `visibilidade-por-equipe`
- Task: `tasks.md` S4 (depende do slice 002)

## 2. Objective

Ciência do recebimento (`FINAL_REPLY_POSTED → CLEANED`) por qualquer `nir`, com
re-check de papel na transação e 403 em `case_ack`; links/redirects de «Reenvios
corrigidos» sem partição próprio/alheio.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/visibilidade-por-equipe/design.md` — D1 (PermissionError, unificação de redirect), D5, D6, D8
- `openspec/changes/visibilidade-por-equipe/specs/case-closure/spec.md` — MODIFIED «Ciência do NIR com encerramento e disposição de dados», «Resultado e casos encerrados visíveis ao criador», «Reenvio corrigido cria novo caso vinculado»
- `apps/cases/closure.py` — `acknowledge_case_receipt` (:176-202: `select_for_update`, check de estado `FINAL_REPLY_POSTED`, check de criador a remover)
- `apps/intake/views.py` — `case_ack` (:828-850), `case_resubmit` (:861+, ramo `elif case.created_by_id == user.pk` :893-898), `_assemble_case_detail_context` (`is_own` :714)
- Testes: `apps/intake/tests/test_closure_views.py`, `apps/cases/tests/test_closure_ack.py`, `test_corrected_resubmission.py`, `test_resubmit_any_nir.py`

## 4. Requirements

- R1: `nir` não-criador registra a ciência de caso `FINAL_REPLY_POSTED` (minimização
  E retenção preservados); evento de ciência com o **operador** como ator; caso já
  encerrado segue recusado com mensagem nomeada (nunca 404 de escopo).
- R2: defesa em profundidade — `acknowledge_case_receipt` troca o check de criador
  por re-check de papel `nir` dentro da transação; `case_ack` traduz
  `PermissionError → PermissionDenied` (403) — nunca 500.
- R3: links de «Reenvios corrigidos» (detalhe original) apontam ao detalhe do caso
  quando ativo e ao detalhe histórico quando encerrado, **para qualquer `nir`**
  (fim da partição `is_own` ativo-sem-link); referência desambiguada preservada.
- R4: redirect de recusa do `case_resubmit` para caso fora do escopo histórico
  unifica no detalhe do caso ativo (remove o ramo `elif case.created_by_id ==
  user.pk` e o fallback à busca); breadcrumb/cancelar já apontam ao histórico.

## 5. Expected blast radius

- `apps/cases/closure.py`, `apps/intake/views.py` (`case_ack`, `case_resubmit`,
  `_assemble_case_detail_context`), template da listagem de reenvios
  (`templates/intake/` onde `is_own` particiona links), testes listados.
- **Não muda**: reenvio corrigido em si (já qualquer `nir`), minimização/purga,
  notificações (Non-Goal), serviços do slice 003.
- Comportamento anterior que permanece: `atomic` + `select_for_update`, modos de
  disposição, evento de supersedição, `correction_created_by` = reenviador.

## 6. Failing-before plan

Failing-before evidence:
- targeted test de serviço (`test_closure_ack.py`): `acknowledge_case_receipt`
  com `nir` não-criador em `FINAL_REPLY_POSTED` → espera `CLEANED`; hoje falha
  com `ValueError("...restrita ao criador...")`.
- targeted test de view (`test_corrected_resubmission.py`/`test_resubmit_any_nir.py`):
  listagem de reenvios com caso corrigido **ativo** criado por outro NIR → espera
  link ao detalhe; hoje exibe referência sem link.
- targeted test: `case_ack` com papel revogado (sessão stalada) → espera 403.

## 7. Implementation constraints

- Re-check de papel no serviço segue o padrão do slice 003 (vínculo no banco,
  dentro da transação).
- Resultado exibido a qualquer `nir` já vem do slice 002 (detalhe) — aqui só a
  AÇÃO de ciência e os links.
- Não alterar eventos/marcadores de minimização (ADR-0010).

## 8. Validation

**Focused validation** — `uv run pytest apps/intake/tests/test_closure_views.py
apps/cases/tests/test_closure_ack.py apps/intake/tests/test_corrected_resubmission.py
apps/intake/tests/test_resubmit_any_nir.py`

**Project-required gates** — `AGENTS.md` §2 ao concluir.

## 9. Acceptance criteria

- [ ] R1: ciência por não-criador OK nos 2 modos; ator correto; estado errado → mensagem nomeada (testes de serviço e view)
- [ ] R2: re-check de papel no serviço + 403 na view com papel revogado (testes)
- [ ] R3: links de reenvios sem partição próprio/alheio; desambiguação preservada (testes)
- [ ] R4: redirect de recusa unificado no detalhe ativo (teste)
- [ ] Failing-before no relatório; gate §2 executado

## 10. Escalation conditions

`BLOCKED_NEEDS_DECISION` se: a remoção do check de criador conflitar com o
contrato transacional da minimização; links dependerem de decisão de UI nova;
contradição spec×código.

## 11. Evidence report

`/tmp/hmd-slice-vis-004-report.md` (mesmo formato do slice 001).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-vis-004-report.md`.
