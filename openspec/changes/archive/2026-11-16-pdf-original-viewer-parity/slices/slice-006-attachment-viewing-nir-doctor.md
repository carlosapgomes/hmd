# Slice 006 — Anexos NIR + médico com visualização do arquivo

## 1. Identity

- **Slice ID:** PVP-006
- **Change ID:** `pdf-original-viewer-parity`
- **Task:** `tasks.md` §6 (`6.1`–`6.4`)

## 2. Objective

Servir os arquivos de anexo (rotas por papel) e exibir sua visualização nos
cards de anexo do NIR e do médico, separados do relatório original.

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/pdf-original-viewer-parity/proposal.md`
- `openspec/changes/pdf-original-viewer-parity/design.md` — D3, D6
- `openspec/changes/pdf-original-viewer-parity/specs/pdf-original-viewer/spec.md` — Embed lazy, Viewer mobile
- `openspec/changes/pdf-original-viewer-parity/specs/attachments/spec.md` — os dois MODIFIED
- `apps/attachments/models.py` (`CaseAttachment`: PK int), `apps/intake/views.py`, `apps/intake/urls.py`
- `apps/doctor/views.py` (`can_access_case`), `apps/doctor/urls.py`
- `templates/intake/case_detail.html` (§ Anexos), `templates/doctor/_case_cards.html` (§ Anexos)
- `.pi/skills/verify-hmd/SKILL.md`, `.pi/skills/verify-hmd/features/detalhe-papeis.md`

## 4. Requirements

- **R1 — Serves de anexo.** `intake:serve_attachment` e
  `doctor:serve_attachment` (`.../attachments/<int>/`, PK int — D6) com os
  guards dos detalhes respectivos, content-type original, `FileResponse` e
  `SAMEORIGIN`; 404 fail-closed (alheio/fora da matriz/inexistente).
- **R2 — Viewers de anexo PDF.** `intake:attachment_pdf_viewer` e
  `doctor:attachment_pdf_viewer` (`.../attachments/<int>/viewer/`, só PDF —
  D3); imagens usam link direto + `<img loading="lazy">`, sem viewer.
- **R3 — Visualização nos cards.** NIR: cada anexo listado ganha link de
  abertura + visualização colapsável (embed lazy PDF / imagem lazy). Médico:
  cada card de anexo ganha bloco de arquivo após evidência (+ link mobile para
  o viewer quando PDF). Sem anexos: páginas idênticas a antes.
- **R4 — Comportamento preservado.** Upload/validação de anexos, pipeline
  OCR/verificação, badges consultivos (mismatch sem bloqueio), re-identificação
  só na renderização.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | testes de guard/content-type/header/404 por papel |
| R2 | testes de guard + render viewer (PDF) |
| R3 | testes de HTML por tipo (PDF/JPEG) e papel + ausência sem anexos |
| R4 | suítes attachments/intake/doctor existentes verdes |

## 5. Expected blast radius

```yaml
expected_files:
  - apps/intake/views.py
  - apps/intake/urls.py
  - apps/doctor/views.py
  - apps/doctor/urls.py
  - templates/intake/case_detail.html
  - templates/doctor/_case_cards.html
  - apps/intake/tests/test_serve_attachment.py (novo)
  - apps/doctor/tests/test_serve_attachment.py (novo)
  - apps/intake/tests/test_attachment_cards.py (novo ou equivalente)
  - apps/doctor/tests/test_attachment_cards.py (novo ou equivalente)
  - .pi/skills/verify-hmd/features/detalhe-papeis.md
  - .pi/skills/verify-hmd/scripts/browser/drive-detalhe-papeis.mjs
allowed_incidental_files:
  - teste existente com expectativa da listagem antiga, citando R3
out_of_scope:
  - scheduler (non-goal); painel (PVP-007); pipeline de anexos (tasks/extraction/verification)
```

**Preservar:** R4 integralmente; mismatch continua consultivo.

## 6. Failing-before plan

```bash
uv run pytest apps/intake/tests/test_serve_attachment.py apps/doctor/tests/test_serve_attachment.py \
  apps/intake/tests/test_attachment_cards.py apps/doctor/tests/test_attachment_cards.py -q
```

**Falhas esperadas:** rotas inexistentes, ausência da visualização nos cards.
Registrar comando, exit status e falhas.

## 7. Implementation constraints

- Follow the repository engineering policy in `AGENTS.md` and preserve existing
  conventions. Prefer the smallest correct change; avoid speculative abstraction
  and unrelated refactoring.
- Nomes de rota seguem a convenção `*_pdf_viewer` (D3); paths D6.
- Re-identificação só na renderização (regra vigente); arquivo servido é o
  original (bytes intactos — mesma política dos serves de documento).
- Não antecipar o painel.

## 8. Validation

**Focused validation**

- RED da seção 6, depois verde; suítes attachments/intake/doctor sem regressão.
- `git diff --check`; sem edição no projeto-fonte.

**Project-required gates**

- Quality gate completo de `AGENTS.md` §2; registrar comandos e exit status.

**Runtime/verification evidence**

- Jornada `detalhe-papeis` (NIR + médico) sobre VERIFY-103 (fixture via
  runVerifyPy: 1 PDF relatório + anexo PDF + JPEG, AWAITING_DOCTOR, declarados,
  criador verify-nir — acessível ao médico como VERIFY-001/002; seed
  compartilhado intocado); anexo PDF (embed lazy + viewer mobile) e JPEG
  (imagem lazy); 1280/390; PNG/snapshot/JSON;
  subprocesso com media root do manifest validado e cleanup de banco + mídia
  da run (padrão PVP-002).

## 9. Acceptance criteria

- [ ] R1–R4 com evidência (automatizada e/ou browser).
- [ ] Content-type original servido; guards = guards dos detalhes.
- [ ] Casos sem anexos visualmente idênticos; mismatch sem bloqueio.
- [ ] Quality gate exit 0; blast radius respeitado ou justificado.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: contradição spec/design; nova decisão de
produto/arquitetura; expansão material de escopo; compatibilidade pública;
migração/FSM; segurança/privacidade/PHI (inclui bytes de anexo); impossibilidade
sem violar o contrato.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-pvp-006-report.md`

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
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-pvp-006-report.md`.
