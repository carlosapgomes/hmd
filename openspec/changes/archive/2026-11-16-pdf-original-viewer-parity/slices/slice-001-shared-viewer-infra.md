# Slice 001 — Infra compartilhada do viewer (helper + template mobile + JS + SAMEORIGIN)

## 1. Identity

- **Slice ID:** PVP-001
- **Change ID:** `pdf-original-viewer-parity`
- **Task:** `tasks.md` §1 (`1.1`–`1.4`)

## 2. Objective

Entregar a base reutilizável dos viewers (helper de `next` seguro, template
mobile, PDF.js vendored, lazy-embed vanilla) e liberar o framing same-origin
nas 3 rotas de serve existentes.

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/pdf-original-viewer-parity/proposal.md`
- `openspec/changes/pdf-original-viewer-parity/design.md` — D2, D3, D4, D5
- `openspec/changes/pdf-original-viewer-parity/specs/pdf-original-viewer/spec.md` — Embed lazy, Viewer mobile
- `apps/intake/views.py` (`serve_document`), `apps/doctor/views.py` (`case_pdf`), `apps/scheduler/views.py` (`case_pdf`)
- `apps/accounts/tests/test_theme_css.py` (padrão de guarda de estático em disco)
- Moldes somente-leitura: `/projects/dev/ats-web/apps/cases/navigation.py`,
  `/projects/dev/ats-web/templates/pdf_viewer/mobile_pdf_viewer.html`,
  `/projects/dev/ats-web/static/js/pdf-viewer.js`, `/projects/dev/ats-web/static/vendor/pdfjs/`

## 4. Requirements

- **R1 — Helper `next` seguro.** `apps/cases/navigation.py` expõe
  `resolve_safe_next_url(request, fallback)` com a semântica D5 (mesmo host,
  `require_https=is_secure()`, fallback otherwise).
- **R2 — Template mobile.** `templates/pdf_viewer/mobile_pdf_viewer.html`
  estende `base.html`, tem nav sticky com `← Voltar` (topo) + botão Voltar no
  rodapé, `data-pdf-url`/`data-fallback-pdf-url` e bloco `extra_js` com PDF.js.
- **R3 — Estáticos.** `static/vendor/pdfjs/` + `static/js/pdf-viewer.js`
  (versão exata do ats-web) e `static/js/pdf-lazy-embed.js` (ouvir
  `show.bs.collapse` no sub-collapse de cada documento, copiar `data-src`→`src`
  uma única vez e somente com breakpoint desktop ativo (`matchMedia`), mais
  listener de mudança de breakpoint para sub-collapses já abertos).
- **R4 — SAMEORIGIN nos serves existentes.** `intake:serve_document`,
  `doctor:case_pdf` e `scheduler:case_pdf` respondem
  `X-Frame-Options: SAMEORIGIN`; guards e corpos inalterados.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | testes unitários (next seguro/inseguro/ausente/`//evil`, https) |
| R2 | teste de render do template com contexto mínimo (Voltar ×2, data attrs, extra_js) |
| R3 | guardas em disco (padrão `test_theme_css.py`: listener por alvo, flag única, guarda de breakpoint + mudança de viewport) + `collectstatic --dry-run`; prova comportamental do lazy no browser fica no runtime de PVP-002 |
| R4 | testes de header por rota (guards existentes seguem verdes) |

## 5. Expected blast radius

```yaml
expected_files:
  - apps/cases/navigation.py
  - apps/cases/tests/test_navigation.py
  - templates/pdf_viewer/mobile_pdf_viewer.html
  - static/vendor/pdfjs/
  - static/js/pdf-viewer.js
  - static/js/pdf-lazy-embed.js
  - apps/intake/views.py
  - apps/doctor/views.py
  - apps/scheduler/views.py
  - apps/intake/tests/test_serve_xframe.py (ou teste equivalente novo)
  - apps/doctor/tests/test_case_pdf_xframe.py (ou equivalente)
  - apps/scheduler/tests/test_case_pdf_xframe.py (ou equivalente)
  - tests/test_viewer_static.py (ou equivalente: template + estáticos)
allowed_incidental_files:
  - ajuste mínimo em teste existente que pine o header antigo, com justificativa
out_of_scope:
  - novas rotas/viewers (PVP-002+)
  - qualquer template de detalhe (PVP-002+)
  - models, migrations, FSM, guards de acesso
```

**Preservar:** corpos/bytes servidos, guards, `Cache-Control` existentes.

## 6. Failing-before plan

Após escrever os testes, antes do código de produção:

```bash
uv run pytest apps/cases/tests/test_navigation.py tests/test_viewer_static.py \
  apps/intake/tests/test_serve_xframe.py apps/doctor/tests/test_case_pdf_xframe.py \
  apps/scheduler/tests/test_case_pdf_xframe.py -q
```

**Falhas esperadas:** `ModuleNotFoundError`/template ausente/estáticos
ausentes e header `DENY` nas 3 rotas. Registrar comando, exit status e falhas
representativas; não obter RED por teste mal escrito.

## 7. Implementation constraints

- Follow the repository engineering policy in `AGENTS.md` and preserve existing
  conventions. Prefer the smallest correct change; avoid speculative abstraction
  and unrelated refactoring.
- Adaptar os moldes ats-web ao contexto HMD (nomes, blocos `base.html`); nunca
  copiar às cegas.
- Não tocar em rotas/URLs existentes; o decorator é a única mudança nas views.
- Não antecipar viewers/cards dos próximos slices.

## 8. Validation

**Focused validation**

- Comando RED da seção 6, depois o mesmo conjunto verde.
- Suítes de serve/guards existentes dos 3 papéis (sem regressão).
- `git diff --check`; nenhuma edição em `/projects/dev/ats-web`.

**Project-required gates**

- Quality gate completo de `AGENTS.md` §2 antes de reportar; registrar comandos
  e exit status.

**Runtime/verification evidence**

- Não aplicável além de `collectstatic --dry-run` (prova de browser do lazy e
  do viewer fica em PVP-002, primeira página consumidora).

## 9. Acceptance criteria

- [ ] R1–R4 têm evidência automatizada correspondente.
- [ ] Header `SAMEORIGIN` nas 3 rotas; bytes/guards inalterados.
- [ ] Template mobile renderiza Voltar ×2 + data attrs + `extra_js`.
- [ ] Estáticos presentes e coletáveis; JS lazy com o contrato `data-src`.
- [ ] Quality gate com exit 0; sem edição fora do blast radius sem justificativa.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: contradição spec/design; necessidade de
nova decisão de produto/arquitetura; expansão material de escopo; mudança de
compatibilidade pública; migração/FSM; nova implicação de segurança/privacidade;
impossibilidade de cumprir sem violar o contrato.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-pvp-001-report.md`

Contém status `READY_FOR_REVIEW` ou `BLOCKED_NEEDS_DECISION`, R1–R4 →
evidência, arquivos alterados, RED/passing-after, comandos + exit status,
expansões de blast radius, riscos e decisões pendentes. (Convenção de path do
repo; difere do default da skill.)

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-pvp-001-report.md`.
