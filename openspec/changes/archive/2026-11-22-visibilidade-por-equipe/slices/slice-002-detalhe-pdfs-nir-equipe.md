# Slice 002 — Detalhe e PDFs/anexos do NIR em escopo de equipe

## 1. Identity

- Slice: `slice-002-detalhe-pdfs-nir-equipe`
- Change: `visibilidade-por-equipe`
- Task: `tasks.md` S2 (depende do slice 001 para o rótulo do breadcrumb)

## 2. Objective

O detalhe do caso ativo e as rotas de PDF/anexo do NIR ficam acessíveis a qualquer
usuário com papel ativo `nir`, de casos de qualquer criador — guard de papel, sem
filtro por criador.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/visibilidade-por-equipe/design.md` — D1, D6, D8
- `openspec/changes/visibilidade-por-equipe/specs/intake-nir/spec.md` — MODIFIED «Meus casos e detalhe do NIR» (cenários de detalhe/serve)
- `openspec/changes/visibilidade-por-equipe/specs/pdf-original-viewer/spec.md` — MODIFIED «Guards de serve e viewer por papel» (NIR)
- `openspec/changes/visibilidade-por-equipe/specs/attachments/spec.md` — MODIFIED «Upload e listagem de anexos pelo NIR»
- `apps/intake/views.py` — `case_detail`, `pdf_viewer`, `attachment_pdf_viewer`, `serve_attachment`, `serve_document`, `_assemble_case_detail_context`
- Testes: `apps/intake/tests/test_case_detail.py`, `test_case_detail_cards.py`, `test_pdf_viewer.py`, `test_serve_attachment.py`, `test_attachment_cards.py`, `test_serve_xframe.py`

## 4. Requirements

- R1: `case_detail` (caso ativo, qualquer status não-`CLEANED`) acessível a `nir`
  não-criador — 404 apenas para caso inexistente; conteúdo íntegro (documentos,
  comunicações, trilha, cards de identificação/procedimentos).
- R2: `serve_document`, `pdf_viewer`, `serve_attachment`, `attachment_pdf_viewer`
  servem caso/alheio de qualquer criador para papel `nir`; papel ativo não-`nir`
  continua negado pelo guard de papel (403) — paridade serve×viewer preservada.
- R3: detalhe histórico de encerrados (rotas da busca) permanece com sua guarda
  própria de escopo histórico — intocado.
- R4: `xframe` e cabeçalhos de serve inalterados; `FAILED` mantém badge e motivo
  técnico fora da exibição.

## 5. Expected blast radius

- `apps/intake/views.py` (as 5 views + helpers de escopo), testes listados.
- **Não muda**: serviços (`_assert_owned_by` — slice 003), `case_ack` (slice 004),
  lógica `is_own` de «Reenvios corrigidos» (slice 004), views médicas/agendador,
  templates (breadcrumb já tratado no 001).
- Comportamento anterior que permanece: referência humana, gate de PDF intacto
  para outros papéis, ordem/estrutura do detalhe.

## 6. Failing-before plan

Failing-before evidence:
- targeted test invertido (`test_case_detail.py`): NIR B abre detalhe de caso
  ativo criado por NIR A → espera 200 com documentos e trilha; hoje falha com 404.
- targeted test invertido (`test_pdf_viewer.py`/`test_serve_attachment.py`):
  NIR B serve documento/anexo de caso alheio → espera 200/FileResponse; hoje 404.

## 7. Implementation constraints

- Remover apenas os filtros `created_by=user` destas views; sem tocar serviços.
- Não antecipar slice 003 (gate/confirmação) nem 004 (ciência/links).
- Menor mudança correta; sem refatoração de apresentação.

## 8. Validation

**Focused validation** — `uv run pytest apps/intake/tests/test_case_detail.py
apps/intake/tests/test_case_detail_cards.py apps/intake/tests/test_pdf_viewer.py
apps/intake/tests/test_serve_attachment.py apps/intake/tests/test_attachment_cards.py
apps/intake/tests/test_serve_xframe.py`

**Project-required gates** — `AGENTS.md` §2 ao concluir.

## 9. Acceptance criteria

- [ ] R1: detalhe alheio 200 (teste invertido); caso inexistente 404
- [ ] R2: serve/viewer alheios 200 p/ `nir`; não-`nir` negado como hoje (testes)
- [ ] R3: rotas de escopo histórico inalteradas (testes existentes verdes)
- [ ] R4: xframe/badge FAILED preservados (testes existentes verdes)
- [ ] Failing-before no relatório; gate §2 executado

## 10. Escalation conditions

`BLOCKED_NEEDS_DECISION` se: guard de papel das rotas de serve revelar-se
divergente do spec `pdf-original-viewer`; surgir dependência de mudança em
serviços; qualquer contradição spec×código.

## 11. Evidence report

`/tmp/hmd-slice-vis-002-report.md` (mesmo formato do slice 001).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-vis-002-report.md`.
