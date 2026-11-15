# Slice 001 — Ícone plano no molde CHD

```yaml
expected_files:
  - static/icons/hmd-base.svg
  - static/icons/hmd-maskable.svg
  - static/icons/hmd-contrast.svg
  - scripts/generate_pwa_icons.py
  - static/icons/icon-*.png, maskable-*.png (regenerados)
  - apps/accounts/tests/test_pwa.py (teste novo estreito)
  - PROJECT_CONTEXT.md (fecho do change)
allowed_incidental_files: []
out_of_scope:
  - manifest, sw.js, theme-color, header, qualquer template
  - qualquer outra superfície visual
```

## Objetivo

Ícones HMD sem faixa: sigla branca centralizada sobre a primária
plana (variante clara para superfícies escuras), PNGs regenerados
deterministicamente.

## Read first

- `AGENTS.md`, `PROJECT_CONTEXT.md`
- `openspec/changes/pwa-icon-simplify/proposal.md`, `design.md`,
  `specs/pwa-manual/spec.md`
- `static/icons/hmd-*.svg`, `scripts/generate_pwa_icons.py`,
  `apps/accounts/tests/test_pwa.py`
- Molde (somente leitura, visual):
  `/projects/dev/ats-web/static/icons/icon-512x512.png`

## Contexto necessário

- Geometria atual: `viewBox` 512, `rx=92` (base), sigla 133px
  centralizada; maskable full-bleed com `scale(0.8)`; faixa via
  `rect` (SVG) e `BAND_*` + `draw_rect` (gerador).
- Testes atuais asserem sigla, cor primária, `CHD` ausente, safe
  zone e precache — nenhum assera a faixa.

## Requisitos verificáveis

- **R1** — Os 3 SVGs não contêm faixa/marcador; base/maskable têm
  fundo plano `#0d4a8a` + "HMD" branca; contraste tem fundo claro +
  "HMD" na primária.
- **R2** — PNGs regenerados pelo script, deterministicamente
  (bytes idênticos em regeneração para diretório temporário).
- **R3** — `--check`, `test_pwa.py` e gate verdes; `PROJECT_CONTEXT.md`
  atualizado para o archive.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | 3 SVGs | teste novo (ausência de faixa/marcador) + existentes |
| R2 | script + PNGs | `--check` + teste de determinismo |
| R3 | gate + `PROJECT_CONTEXT.md` | `ruff + format + mypy + pytest` |

## Plano de testes

### RED

- Comando: `uv run pytest apps/accounts/tests/test_pwa.py -q` (com o
  teste novo antes da mudança)
- Falha esperada: faixa ainda presente nos SVGs.

### GREEN / verificação local

- `uv run pytest apps/accounts/tests/test_pwa.py -q`, `mypy`,
  `ruff check . && uv run ruff format --check .` — exit 0
- captura do PNG 512 vs ícone CHD lado a lado para revisão do dono

## Critérios de aceitação

- [ ] R1–R3 verdes; nenhum `rect` de faixa restante (SVG e gerador)
- [ ] Docstring do gerador sem menção a faixa/marcador

## Relatório de evidência

`/tmp/hmd-slice-p001-report.md` (`READY_FOR_REVIEW` ou
`BLOCKED_NEEDS_DECISION`, com requirements→evidence, arquivos,
failing-before/passing-after e comandos com exit status).

## Handoff

Implementar apenas este slice. Escalar se qualquer teste existente
depender da faixa. Ao concluir, o change está pronto para revisão
final (não arquivar — archive é papel do parent).
