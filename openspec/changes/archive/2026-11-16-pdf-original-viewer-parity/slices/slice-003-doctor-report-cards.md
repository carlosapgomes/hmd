# Slice 003 — Médico: viewer + dois cards no partial compartilhado

## 1. Identity

- **Slice ID:** PVP-003
- **Change ID:** `pdf-original-viewer-parity`
- **Task:** `tasks.md` §3 (`3.1`–`3.4`)

## 2. Objective

Dar ao médico os dois cards do relatório (com placeholder de texto
indisponível) no partial `_case_cards.html`, cobrindo detalhe e decisão.

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/pdf-original-viewer-parity/proposal.md`
- `openspec/changes/pdf-original-viewer-parity/design.md` — D1, D2, D3, D8
- `openspec/changes/pdf-original-viewer-parity/specs/pdf-original-viewer/spec.md`
- `apps/doctor/views.py` (`case_detail`, `case_pdf`, `_render_decision_page`), `apps/doctor/urls.py`, `apps/doctor/presenters.py`
- `templates/doctor/_case_cards.html`
- `.pi/skills/verify-hmd/SKILL.md`, `.pi/skills/verify-hmd/features/detalhe-papeis.md`
- Molde somente-leitura: `/projects/dev/ats-web/templates/doctor/decision.html` (§ Texto Extraído/PDF Viewer)

## 4. Requirements

- **R1 — Rota viewer.** `doctor:pdf_viewer`
  (`case/<uuid>/pdf/<position>/viewer/`) sob `can_access_case` (mesmo guard do
  `case_pdf`), `?next=` seguro, fail-closed idêntico ao serve (fora da matriz →
  403 pelo guard vigente de subtipo — decisão do dono 2026-11-15;
  inexistente/sem arquivo → 404).
- **R2 — Cards no partial.** Card "Documentos" de `_case_cards.html` trocado
  pelos dois cards (D8: após Prior-case); texto vazio exibe placeholder de
  indisponibilidade (não omite); lazy + links como PVP-002.
- **R3 — Cobertura dupla.** Detalhe read-only e página de decisão renderizam os
  mesmos cards (edição única no partial).
- **R4 — Comportamento preservado.** Ordem clínica, decisão por procedimento,
  locks, prior-case, anexos-consultivos e referência humana intactos.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | testes de guard matriz/next/404 |
| R2 | testes de HTML (cards, placeholder, `data-src`, links) |
| R3 | testes nas duas rotas (`case_detail` e `case_decide`) |
| R4 | suíte doctor existente verde |

## 5. Expected blast radius

```yaml
expected_files:
  - apps/doctor/views.py
  - apps/doctor/urls.py
  - apps/doctor/presenters.py (só se pdf_items exigir; preferir view)
  - templates/doctor/_case_cards.html
  - apps/doctor/tests/test_pdf_viewer.py (novo)
  - apps/doctor/tests/test_detail_cards.py (novo ou equivalente)
  - .pi/skills/verify-hmd/features/detalhe-papeis.md
  - .pi/skills/verify-hmd/scripts/browser/drive-detalhe-papeis.mjs
allowed_incidental_files:
  - teste doctor existente com expectativa do card antigo, citando R2
out_of_scope:
  - NIR/scheduler/dashboard; anexos-arquivo (PVP-006); locks/services
```

**Preservar:** R4 integralmente; `decide.html`/`case_detail.html` só via partial.

## 6. Failing-before plan

```bash
uv run pytest apps/doctor/tests/test_pdf_viewer.py apps/doctor/tests/test_detail_cards.py -q
```

**Falhas esperadas:** rota inexistente, ausência dos cards/placeholder no HTML
das duas páginas. Registrar comando, exit status e falhas.

## 7. Implementation constraints

- Follow the repository engineering policy in `AGENTS.md` and preserve existing
  conventions. Prefer the smallest correct change; avoid speculative abstraction
  and unrelated refactoring.
- Uma edição no partial; não duplicar markup em `decide.html`/`case_detail.html`.
- Guard via `can_access_case` (predicado único, nunca duplicado).
- Não antecipar outros papéis/anexos.

## 8. Validation

**Focused validation**

- RED da seção 6, depois verde; suíte doctor completa sem regressão.
- `git diff --check`; sem edição no projeto-fonte.

**Project-required gates**

- Quality gate completo de `AGENTS.md` §2; registrar comandos e exit status.

**Runtime/verification evidence**

- Jornada `detalhe-papeis` (médico) sobre VERIFY-101 (runVerifyPy): detalhe + decisão em 1280/390
  (lazy por alvo 1×, 0 fetch mobile, viewer Voltar — contrato D2 provado em PVP-002); PNG/snapshot/JSON.
  O subprocesso que cria arquivos recebe o media root do manifest validado, conforme PVP-002.

## 9. Acceptance criteria

- [ ] R1–R4 com evidência (automatizada e/ou browser).
- [ ] Placeholder (não omissão) com texto vazio; cards idênticos nas 2 páginas.
- [ ] Fora da matriz = 404; next inseguro cai no detalhe/decisão.
- [ ] Quality gate exit 0; blast radius respeitado ou justificado.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: contradição spec/design; nova decisão de
produto/arquitetura; expansão material de escopo; compatibilidade pública;
migração/FSM; segurança/privacidade (inclui re-identificação); impossibilidade
sem violar o contrato.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-pvp-003-report.md`

Status, R1–R4 → evidência, arquivos, RED/passing-after, comandos + exit status,
paths do run, expansões, riscos, decisões pendentes.

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-pvp-003-report.md`.
