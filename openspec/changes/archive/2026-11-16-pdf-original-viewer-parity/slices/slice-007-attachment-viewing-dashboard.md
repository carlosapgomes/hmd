# Slice 007 — Anexos no painel + verificação final da change

## 1. Identity

- **Slice ID:** PVP-007
- **Change ID:** `pdf-original-viewer-parity`
- **Task:** `tasks.md` §7 (`7.1`–`7.4`)

## 2. Objective

Exibir os anexos com visualização do arquivo no detalhe do painel e fechar a
change com a verificação cruzada dos 4 papéis.

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/pdf-original-viewer-parity/proposal.md`
- `openspec/changes/pdf-original-viewer-parity/design.md` — D3, D6, D8
- `openspec/changes/pdf-original-viewer-parity/specs/attachments/spec.md` — ADDED painel
- `apps/dashboard/views.py`, `apps/dashboard/urls.py`, `templates/dashboard/case_detail.html`
- `apps/intake/views.py` + `apps/doctor/views.py` (serves de anexo de PVP-006, como molde)
- `.pi/skills/verify-hmd/SKILL.md`, `.pi/skills/verify-hmd/features/painel-visual.md`,
  `.pi/skills/verify-hmd/features/detalhe-papeis.md`

## 4. Requirements

- **R1 — Serve + viewer de anexo no painel.** `dashboard:serve_attachment` e
  `dashboard:attachment_pdf_viewer` sob `manager`/`admin` (403 fora; 404
  inexistente), content-type original, `SAMEORIGIN`, `no-store` no serve.
- **R2 — Seção de anexos no painel.** Nome, status e visualização do arquivo
  (embed lazy PDF / imagem lazy / link mobile viewer para PDF); seção omitida
  sem anexos; posição após os cards de documento (D8).
- **R3 — Fechamento da change.** Gate completo verde na árvore final e
  regressão browser dos 4 papéis (cards + lazy + viewers + anexos) sem falha.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | testes de guard/content-type/headers/404 |
| R2 | testes de HTML (seção, tipos, omissão, posição) |
| R3 | gate final + jornadas `detalhe-papeis` e `painel-visual` verdes |

## 5. Expected blast radius

```yaml
expected_files:
  - apps/dashboard/views.py
  - apps/dashboard/urls.py
  - templates/dashboard/case_detail.html
  - apps/dashboard/tests/test_serve_attachment.py (novo)
  - apps/dashboard/tests/test_attachment_section.py (novo ou equivalente)
  - .pi/skills/verify-hmd/features/painel-visual.md
  - .pi/skills/verify-hmd/scripts/browser/drive-painel-visual.mjs
allowed_incidental_files:
  - nenhum previsto (fixtures via runVerifyPy nos drives, padrão PVP-002/006)
out_of_scope:
  - NIR/médico/agendador (só regressão via suíte/jornadas); métricas/encerramento
```

**Preservar:** todo o comportamento dos slices anteriores (regressão, não reforma).

## 6. Failing-before plan

```bash
uv run pytest apps/dashboard/tests/test_serve_attachment.py \
  apps/dashboard/tests/test_attachment_section.py -q
```

**Falhas esperadas:** rotas inexistentes, ausência da seção no HTML. Registrar
comando, exit status e falhas.

## 7. Implementation constraints

- Follow the repository engineering policy in `AGENTS.md` and preserve existing
  conventions. Prefer the smallest correct change; avoid speculative abstraction
  and unrelated refactoring.
- Espelhar PVP-006 (mesmo padrão de serve/viewer); nada de novo além do guard.
- R3 é verificação: regressão encontrada aqui volta como defeito do slice de
  origem no relatório (sem reabrir escopo silenciosamente).

## 8. Validation

**Focused validation**

- RED da seção 6, depois verde; suíte dashboard + suíte completa sem regressão.
- `git diff --check`; sem edição no projeto-fonte.

**Project-required gates**

- Quality gate completo de `AGENTS.md` §2 (é o gate final da change); registrar
  comandos e exit status.

**Runtime/verification evidence**

- Jornadas `detalhe-papeis` (NIR/médico/agendador, sobre VERIFY-101/102/103) +
  `painel-visual` (manager, sobre VERIFY-101/103) na árvore final: cards,
  lazy por alvo 1×, viewers com Voltar, anexos PDF/JPEG;
  Em `painel-visual`, concluir primeiro todos os passos e asserts existentes
  de exatamente dois casos (manager/admin e reset); só então criar as fixtures
  VERIFY-101/103 e executar as novas checagens de detalhe. Preservar os asserts
  de VERIFY-001/002 e não repetir os de contagem após criar fixtures. Cada
  subprocesso que cria arquivos recebe o media root do manifest validado,
  conforme PVP-002. Registrar PNG/snapshot/JSON em 1280/390; cleanup remove
  apenas banco e mídia da run.

## 9. Acceptance criteria

- [ ] R1–R3 com evidência (automatizada e/ou browser).
- [ ] Seção omitida sem anexos; 403 fora de manager/admin.
- [ ] Gate final exit 0; jornadas dos 4 papéis sem falha.
- [ ] Blast radius respeitado ou justificado; regressões (se houver) mapeadas ao
      slice de origem.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: contradição spec/design; nova decisão de
produto/arquitetura; expansão material de escopo; compatibilidade pública;
migração/FSM; segurança/privacidade; regressão que exija redesenho fora deste
slice; impossibilidade sem violar o contrato.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-pvp-007-report.md`

Status, R1–R3 → evidência, arquivos, RED/passing-after, comandos + exit status,
paths dos runs, expansões, riscos, decisões pendentes + mapa de regressões.

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-pvp-007-report.md`.
