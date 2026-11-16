# Slice 002 — NIR: viewer + dois cards do relatório

## 1. Identity

- **Slice ID:** PVP-002
- **Change ID:** `pdf-original-viewer-parity`
- **Task:** `tasks.md` §2 (`2.1`–`2.4`)

## 2. Objective

Dar ao NIR os cards `Texto Extraído do PDF` e `Visualizar PDF Original`
(lazy no desktop, viewer dedicado no mobile) no detalhe do caso.

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/pdf-original-viewer-parity/proposal.md`
- `openspec/changes/pdf-original-viewer-parity/design.md` — D1, D2, D3, D8
- `openspec/changes/pdf-original-viewer-parity/specs/pdf-original-viewer/spec.md`
- `apps/intake/views.py` (`case_detail`, `serve_document`), `apps/intake/urls.py`
- `templates/intake/case_detail.html`
- `apps/cases/navigation.py`, `templates/pdf_viewer/mobile_pdf_viewer.html` (de PVP-001)
- `.pi/skills/verify-hmd/SKILL.md`, `.pi/skills/verify-hmd/features/detalhe-papeis.md`
- Molde somente-leitura: `/projects/dev/ats-web/templates/intake/case_detail.html` (§ Texto Extraído/PDF Viewer)

## 4. Requirements

- **R1 — Rota viewer.** `intake:pdf_viewer`
  (`cases/<uuid>/documents/<pk>/viewer/`) com guard de criador, `?next=` via
  `resolve_safe_next_url` (fallback = detalhe), 404 para caso alheio/
  inexistente/documento inexistente/sem PDF.
- **R2 — Cards no detalhe.** `pdf_items` (D1) montado na view; tabela
  "Documentos" substituída pelos dois cards fechados por padrão; card de texto
  omitido sem `extracted_text`; sub-collapse por documento com embed sem `src`
  (+`data-src`), link mobile (`d-md-none`), "Abrir em nova aba" e bloco
  `<noscript>` com links diretos fora dos collapses.
- **R3 — Lazy real.** Abrir o card externo busca 0 PDFs; expandir um
  sub-collapse busca só o seu PDF, uma única vez (doc 2 segue sem `src` —
  caso VERIFY-101 com 2 PDFs sintéticos reais + texto, criado na jornada via
  runVerifyPy sem tocar o seed compartilhado; prova no browser);
  mobile nunca busca os embeds; resize para desktop com sub-collapse aberto
  carrega o alvo; sem JS os links do bloco `<noscript>` seguem funcionais
  (prova no browser sem JS).
- **R4 — Comportamento preservado.** Gate actions, reenvio, comunicações,
  anexos-listagem, referência humana e guards intactos.
- **R5 — Mídia isolada por run.** `MEDIA_ROOT` da verify por run
  (`HMD_VERIFY_MEDIA_ROOT`, mesmo padrão do `staticRoot`; fallback temp fora
  do checkout); o manifest identifica o diretório de mídia validado sob a run.
  Toda invocação `runVerifyPy` que cria arquivos recebe explicitamente
  `HMD_VERIFY_MEDIA_ROOT` do manifest validado, igual ao do servidor. Cleanup
  remove somente a mídia da run; `media/` do checkout nunca recebe arquivos.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | testes de guard/next/fallback/404 por cenário |
| R2 | testes de HTML (cards, `data-src` sem `src`, links, omissão sem texto) |
| R3 | jornada browser `detalhe-papeis` sobre VERIFY-101 via runVerifyPy (rede: 0 fechado, 0 com card externo aberto, 1 por documento expandido, 0 no mobile, load no resize p/ desktop) + browser sem-JS (noscript) |
| R4 | suíte intake existente verde |
| R5 | teste de settings e cleanup seguro; prova de que subprocesso e servidor usam o mesmo root validado, servem arquivo da run e o removem no cleanup, preservando outras runs e `media/` do checkout |

## 5. Expected blast radius

```yaml
expected_files:
  - apps/intake/views.py
  - apps/intake/urls.py
  - templates/intake/case_detail.html
  - apps/intake/tests/test_pdf_viewer.py (novo)
  - apps/intake/tests/test_case_detail_cards.py (novo ou equivalente)
  - config/settings/verify.py (MEDIA_ROOT por run)
  - .pi/skills/verify-hmd/scripts/control-hmd (HMD_VERIFY_MEDIA_ROOT + manifest + cleanup)
  - apps/accounts/tests/test_verify_media_isolation.py (novo)
  - .pi/skills/verify-hmd/features/detalhe-papeis.md
  - .pi/skills/verify-hmd/scripts/browser/drive-detalhe-papeis.mjs
allowed_incidental_files:
  - teste intake existente com expectativa da tabela antiga, citando R2
out_of_scope:
  - doctor/scheduler/dashboard (PVP-003/004/005)
  - visualização de arquivo de anexo (PVP-006)
  - serve_document (bytes/guard — só header mudou em PVP-001)
```

**Preservar:** R4 integralmente.

## 6. Failing-before plan

```bash
uv run pytest apps/intake/tests/test_pdf_viewer.py apps/intake/tests/test_case_detail_cards.py \
  apps/accounts/tests/test_verify_media_isolation.py -q
```

**Falhas esperadas:** 404 em rota inexistente (NoReverseMatch), ausência dos
cards/`data-src` no HTML, MEDIA_ROOT fora da run. Registrar comando, exit
status e falhas.

## 7. Implementation constraints

- Follow the repository engineering policy in `AGENTS.md` and preserve existing
  conventions. Prefer the smallest correct change; avoid speculative abstraction
  and unrelated refactoring.
- URLs montadas na view (D1); template só itera.
- `pdf-lazy-embed.js` carregado apenas onde os cards existem.
- Não antecipar outros papéis/anexos.
- Seed compartilhado: intocado — fixtures via runVerifyPy na jornada (IDs
  VERIFY-101+, fora das faixas do seed e dos drives); nunca tocar bancos fora
  de `hmd_verify_*` ou `media/` do checkout.
- Fixtures com IDs/nomes distintos; passos existentes que selecionam por
  VERIFY-001 seguem resolvendo igual.
- Antes de criar arquivos, validar no manifest que `mediaRoot` pertence à run;
  passar explicitamente `HMD_VERIFY_MEDIA_ROOT=mediaRoot` ao subprocesso e
  conferir que coincide com o root do servidor. Rejeitar paths inválidos ou
  fora da run; o cleanup nunca remove diretórios externos nem de outra run.

## 8. Validation

**Focused validation**

- RED da seção 6, depois verde; suíte intake completa sem regressão.
- `git diff --check`; sem edição no projeto-fonte.

**Project-required gates**

- Quality gate completo de `AGENTS.md` §2; registrar comandos e exit status.

**Runtime/verification evidence**

> Decisão do dono (2026-11-15, opção C — emenda pós review-3): no modo headless
> o plugin de PDF do Chromium pinado (1.64.0/chromium-1248) não busca nem
> compõe `<embed application/pdf>` com `src` via JS (comprovado empiricamente);
> no modo headed-Xvfb o plugin **busca** o embed — o fetch é provado
> DIRETAMENTE pelo drive (GET sem `?probe=1` no server.log após a expansão,
> target-only; reprodutível em 2 runs). A opção C cobre portanto apenas a
> **renderização**: viewer PDF.js mobile renderizando o PDF pela MESMA rota
> de serve (canvas não-branco). O drive permanece fail-loud; a limitação do
> headless fica documentada nos experimentos e na feature md. Spec mantém
> `<embed>`.

- Jornada `detalhe-papeis` (login real, instância isolada, cleanup) sobre
  VERIFY-101 (fixture via runVerifyPy, 2 PDFs): detalhe NIR em 1280 (card externo = 0 fetch; expansão carrega
  1× só o alvo; embed renderiza; doc 2 sem `src`) e 390 (só link, 0 fetch de
  embed; viewer com Voltar topo/rodapé funcionais); transição 390→1280 com
  sub-collapse aberto carrega o alvo; passada sem JS (links noscript visíveis
  e funcionais); PNG/snapshot/JSON. Comprovar que PDF criado pelo subprocesso
  está no root da run e é servido pelo servidor; após cleanup, provar que foi
  removido e que outra run e `media/` do checkout permanecem intactas.

## 9. Acceptance criteria

- [ ] R1–R5 com evidência (automatizada e/ou browser).
- [ ] HTML sem `src` nos embeds fechados; `data-src` = rota de serve do NIR.
- [ ] Next inseguro cai no detalhe; caso alheio = 404.
- [ ] Rede prova 0 fetch fechado / 1 fetch na expansão.
- [ ] Mídia da run isolada; `media/` do checkout intacta e run limpa pós-cleanup.
- [ ] Quality gate exit 0; blast radius respeitado ou justificado.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: contradição spec/design; nova decisão de
produto/arquitetura; expansão material de escopo; compatibilidade pública;
migração/FSM; segurança/privacidade; impossibilidade sem violar o contrato.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-pvp-002-report.md`

Status, R1–R5 → evidência, arquivos, RED/passing-after, comandos + exit status,
paths do run (PNG/snapshot/JSON), expansões, riscos, decisões pendentes.

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-pvp-002-report.md`.
