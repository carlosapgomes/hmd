# Slice 005 — Painel: serve + viewer + dois cards

## 1. Identity

- **Slice ID:** PVP-005
- **Change ID:** `pdf-original-viewer-parity`
- **Task:** `tasks.md` §5 (`5.1`–`5.4`)

## 2. Objective

Dar ao supervisor (manager/admin) serve próprio de PDF e os dois cards do
relatório no detalhe do painel.

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/pdf-original-viewer-parity/proposal.md`
- `openspec/changes/pdf-original-viewer-parity/design.md` — D1, D3, D8
- `openspec/changes/pdf-original-viewer-parity/specs/pdf-original-viewer/spec.md`
- `openspec/changes/pdf-original-viewer-parity/specs/dashboard/spec.md`
- `apps/dashboard/views.py` (`case_detail`, `_case_detail_context`), `apps/dashboard/urls.py`
- `templates/dashboard/case_detail.html`
- `apps/scheduler/views.py` (`case_pdf` — molde de serve por `position`)
- `.pi/skills/verify-hmd/SKILL.md`, `.pi/skills/verify-hmd/features/painel-visual.md`

## 4. Requirements

- **R1 — Rotas de serve + viewer.** `dashboard:case_pdf`
  (`case/<uuid>/pdf/<position>/`, `FileResponse` + `SAMEORIGIN` +
  `Cache-Control: no-store`) e `dashboard:pdf_viewer` (`.../viewer/`), ambos
  sob `manager`/`admin` (outro papel = 403; inexistente = 404).
- **R2 — Cards no detalhe.** `pdf_items` no contexto; dois cards após
  "Procedimentos declarados", antes da trilha (D8); texto vazio omite o card.
- **R3 — Comportamento preservado.** Métricas, filtros, lista, trilha,
  encerramento administrativo e referência humana intactos.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | testes de guard (403/404), bytes, headers (SAMEORIGIN + no-store), next |
| R2 | testes de HTML (posição, `data-src`, links, omissão) |
| R3 | suíte dashboard existente verde |

## 5. Expected blast radius

```yaml
expected_files:
  - apps/dashboard/views.py
  - apps/dashboard/urls.py
  - templates/dashboard/case_detail.html
  - apps/dashboard/tests/test_case_pdf.py (novo)
  - apps/dashboard/tests/test_detail_cards.py (novo ou equivalente)
  - .pi/skills/verify-hmd/features/painel-visual.md
  - .pi/skills/verify-hmd/scripts/browser/drive-painel-visual.mjs
allowed_incidental_files:
  - nenhum previsto
out_of_scope:
  - NIR/médico/agendador; anexos do painel (PVP-007); métricas/filtros/encerramento
```

**Preservar:** R3 integralmente.

## 6. Failing-before plan

```bash
uv run pytest apps/dashboard/tests/test_case_pdf.py apps/dashboard/tests/test_detail_cards.py -q
```

**Falhas esperadas:** rotas inexistentes (NoReverseMatch/404), ausência dos
cards no HTML. Registrar comando, exit status e falhas.

## 7. Implementation constraints

- Follow the repository engineering policy in `AGENTS.md` and preserve existing
  conventions. Prefer the smallest correct change; avoid speculative abstraction
  and unrelated refactoring.
- Espelhar `scheduler:case_pdf` (molde mais próximo: serve por `position`,
  no-store); guard = `role_required("manager", "admin")` do detalhe.
- Não antecipar anexos (PVP-006/007).

## 8. Validation

**Focused validation**

- RED da seção 6, depois verde; suíte dashboard completa sem regressão.
- `git diff --check`; sem edição no projeto-fonte.

**Project-required gates**

- Quality gate completo de `AGENTS.md` §2; registrar comandos e exit status.

**Runtime/verification evidence**

- Jornada `painel-visual`: executar primeiro todos os passos e asserts existentes
  que exigem exatamente os dois casos do seed, incluindo manager/admin e reset.
  Somente depois criar VERIFY-101 via `runVerifyPy` e verificar seu detalhe com
  cards em 1280/390 (lazy por alvo 1×, 0 fetch mobile, viewer Voltar); não
  executar novamente os asserts de exatamente dois casos após criar a fixture.
  Preservar as verificações existentes de VERIFY-001/002; registrar
  PNG/snapshot/JSON. O subprocesso que cria arquivos recebe o media root do
  manifest validado conforme PVP-002.

## 9. Acceptance criteria

- [ ] R1–R3 com evidência (automatizada e/ou browser).
- [ ] Headers `SAMEORIGIN` + `no-store` no serve; 403 fora de manager/admin.
- [ ] Cards na posição D8; trilha/encerramento intactos.
- [ ] Quality gate exit 0; blast radius respeitado ou justificado.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: contradição spec/design; nova decisão de
produto/arquitetura; expansão material de escopo; compatibilidade pública;
migração/FSM; segurança/privacidade; impossibilidade sem violar o contrato.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-pvp-005-report.md`

Status, R1–R3 → evidência, arquivos, RED/passing-after, comandos + exit status,
paths do run, expansões, riscos, decisões pendentes.

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-pvp-005-report.md`.
