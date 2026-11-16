# Slice 004 — Agendador: viewer + dois cards sob o gate

## 1. Identity

- **Slice ID:** PVP-004
- **Change ID:** `pdf-original-viewer-parity`
- **Task:** `tasks.md` §4 (`4.1`–`4.4`)

## 2. Objective

Dar ao agendador os dois cards do relatório, exibidos somente sob o gate
`can_view_pdf` vigente (caso processado por ele, pós-decisão).

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/pdf-original-viewer-parity/proposal.md`
- `openspec/changes/pdf-original-viewer-parity/design.md` — D1, D3, D7, D8
- `openspec/changes/pdf-original-viewer-parity/specs/pdf-original-viewer/spec.md`
- `openspec/changes/pdf-original-viewer-parity/specs/scheduling/spec.md`
- `apps/scheduler/views.py` (`case_pdf`, detalhe, `can_view_pdf`), `apps/scheduler/urls.py`
- `templates/scheduler/case_detail.html`
- `.pi/skills/verify-hmd/SKILL.md`, `.pi/skills/verify-hmd/features/detalhe-papeis.md`

## 4. Requirements

- **R1 — Rota viewer.** `scheduler:pdf_viewer`
  (`case/<uuid>/pdf/<position>/viewer/`) com o mesmo gate fail-closed do
  `case_pdf` (`scheduled_by` + status; reaberto = 404).
- **R2 — Cards sob o gate.** Os dois cards dentro do `{% if can_view_pdf %}`
  existente (D7); fora do gate, nenhum dos dois aparece; texto vazio omite o
  card de texto.
- **R3 — Comportamento preservado.** Visão limitada ao necessário (sem demais
  artefatos), confirmar/negar/intercorrência, comunicações, referência humana.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | testes de gate (próprio/outro/reaberto → 200/404/404) + next |
| R2 | testes de HTML dentro/fora do gate + omissão sem texto |
| R3 | suíte scheduler existente verde |

## 5. Expected blast radius

```yaml
expected_files:
  - apps/scheduler/views.py
  - apps/scheduler/urls.py
  - templates/scheduler/case_detail.html
  - apps/scheduler/tests/test_pdf_viewer.py (novo)
  - apps/scheduler/tests/test_detail_cards.py (novo ou equivalente)
  - .pi/skills/verify-hmd/features/detalhe-papeis.md
  - .pi/skills/verify-hmd/scripts/browser/drive-detalhe-papeis.mjs
allowed_incidental_files:
  - teste scheduler existente com expectativa do bloco antigo, citando R2
out_of_scope:
  - NIR/médico/painel; anexos (sem escopo no agendador — non-goal); services de agendamento
```

**Preservar:** R3 integralmente; semântica do gate byte a byte.

## 6. Failing-before plan

```bash
uv run pytest apps/scheduler/tests/test_pdf_viewer.py apps/scheduler/tests/test_detail_cards.py -q
```

**Falhas esperadas:** rota inexistente, ausência dos cards no HTML sob o gate.
Registrar comando, exit status e falhas.

## 7. Implementation constraints

- Follow the repository engineering policy in `AGENTS.md` and preserve existing
  conventions. Prefer the smallest correct change; avoid speculative abstraction
  and unrelated refactoring.
- Reutilizar o predicado/condição do gate; nunca reimplementar a checagem.
- Texto extraído é a ÚNICA adição à visão limitada (decisão D7); nada mais.
- Não antecipar painel/anexos.

## 8. Validation

**Focused validation**

- RED da seção 6, depois verde; suíte scheduler completa sem regressão.
- `git diff --check`; sem edição no projeto-fonte.

**Project-required gates**

- Quality gate completo de `AGENTS.md` §2; registrar comandos e exit status.

**Runtime/verification evidence**

- Jornada `detalhe-papeis` (agendador): dentro do gate sobre VERIFY-102
  (fixture via runVerifyPy: scheduled_by=verify-scheduler, status pós-decisão,
  1 PDF + texto; transições pelos métodos/serviços existentes; seed
  compartilhado intocado) e fora do gate sobre VERIFY-101 (ausência total);
  1280/390 com 0 fetch mobile; PNG/snapshot/JSON;
  o subprocesso que cria arquivos recebe o media root do manifest validado
  (padrão PVP-002).

## 9. Acceptance criteria

- [ ] R1–R3 com evidência (automatizada e/ou browser).
- [ ] Fora do gate: zero menção a texto/PDF na página; reaberto = 404.
- [ ] Nenhum outro artefato do pipeline vaza para a visão.
- [ ] Quality gate exit 0; blast radius respeitado ou justificado.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: contradição spec/design; nova decisão de
produto/arquitetura; expansão material de escopo; compatibilidade pública;
migração/FSM; segurança/privacidade (inclui qualquer vazamento além do D7);
impossibilidade sem violar o contrato.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-pvp-004-report.md`

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
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-pvp-004-report.md`.
