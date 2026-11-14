# Slice 001 — Upload com drag-and-drop e preview

```yaml
expected_files:
  - templates/intake/home.html (upload-zone + preview + texts)
  - templates/intake/corrected_resubmission.html (mesmo widget, script carregado, caminho de 1 PDF)
  - static/js/intake-upload.js (rework sobre o molde upload.js do ats-web)
  - apps/intake/tests/test_upload_ui.py (novos/estendidos, tipados)
  - .pi/skills/verify-hmd/{features,scripts}/ (jornada upload-dragdrop)
allowed_incidental_files: []
out_of_scope:
  - limites, validação server-side, semântica de lote homogêneo (intactos)
  - seção de anexos do change 10 (preservada; só reestilizada se o molde exigir)
  - combobox (slice 002), painel, filas
```

## Objetivo

Envio NIR com drag-and-drop e preview no padrão ats-web, sobre os
fields, limites e semântica HMD.

## Read first

- `AGENTS.md`, `PROJECT_CONTEXT.md`
- `openspec/changes/ui-parity-intake-detail/proposal.md`, `design.md` (D1, D5–D6), `specs/intake-nir/spec.md` + `specs/attachments/spec.md` (reconciliação com o comportamento vigente)
- `templates/intake/home.html`, `static/js/intake-upload.js`, `apps/intake/forms.py`, `apps/intake/tests/test_upload_ui.py`
- Molde (somente leitura): `/projects/dev/ats-web/templates/intake/intake_home.html`, `/projects/dev/ats-web/static/js/upload.js`

## Contexto necessário

- Fields HMD: `documents` (multi-PDF) + `attachments` (só com 1
  relatório) + regra de lote homogêneo; limites e erros server-side
  inalterados.
- Molde ats-web: zona drag-and-drop, preview (lista, contagem,
  tamanho), alertas de lote; adaptar names/limites/textos.

## Requisitos verificáveis

- **R1** — Zona aceita arrastar/soltar e clique; preview lista
  arquivos, contagem e tamanho total; lote inválido mostra o erro
  server-side sem perder o contexto. Autoridade é do servidor:
  lote misto cria os válidos e nomeia os inválidos; nomes duplicados
  geram um erro por arquivo; anexos com >1 relatório geram erro
  não-bloqueante; arquivos arrastados são os submetidos.
- **R2** — Sem JS, o form nativo submete o mesmo lote (prova no
  browser, não só teste de form).
- **R3** — Reenvio corrigido (1 PDF) carrega o script e usa o mesmo
  widget; anexo válido/inválido comporta-se como no envio.
- **R4** — Sem gate novo de confirmação: anexo submete sem
  confirmação extra (o gate do ats-web NÃO é portado).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `home.html`, `intake-upload.js` | testes de marcadores + jornada (lote válido/inválido/misto/duplicado; anexos 1→2→1 relatórios) |
| R2 | `home.html` | jornada no browser sem JS |
| R3 | `corrected_resubmission.html`, form | testes + jornada do fluxo de reenvio |
| R4 | `home.html`, `intake-upload.js` | teste: anexo submete sem confirmação; nenhum bloqueio de submit no JS |

## Plano de testes

### RED

- Comando: `uv run pytest apps/intake/tests/test_upload_ui.py -q` (testes novos antes)
- Falha esperada: zona/preview/marcadores ausentes.

### GREEN / verificação local

- `uv run pytest apps/intake -q`, `mypy`, `ruff check/format` — exit 0
- jornada verify-hmd + capturas vs ats-web

## Critérios de aceitação

- [ ] R1–R4 verdes; limites e mensagens de erro inalterados
- [ ] Anexos intactos; nenhum gate de confirmação adicionado

## Relatório de evidência

`/tmp/hmd-slice-i001-report.md` (`READY_FOR_REVIEW` ou
`BLOCKED_NEEDS_DECISION`, com requirements→evidence, arquivos,
failing-before/passing-after e comandos com exit status).

## Handoff

Implementar apenas este slice. Escalar se o molde exigir dado novo
do backend ou flexibilizar limite/regra de lote. Não iniciar o slice 002.
