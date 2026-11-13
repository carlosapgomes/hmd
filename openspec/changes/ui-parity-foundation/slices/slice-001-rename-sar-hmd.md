# Slice 001 — Rename SAR–HMD

```yaml
expected_files:
  - config/settings/base.py
  - config/settings/test.py
  - static/manifest.json
  - static/icons/hmd-base.svg
  - static/icons/hmd-maskable.svg
  - static/icons/hmd-contrast.svg (novo, variante para superfícies escuras)
  - scripts/generate_pwa_icons.py
  - static/icons/icon-*.png, maskable-*.png (regenerados)
  - static/js/sw.js (`CACHE_NAME` → v2 + precache dos novos SVGs;
    bypass de PDF/POST preservado)
  - templates/base.html (marca/título)
  - templates/accounts/manual.html (título/conteúdo de marca)
  - templates/accounts/login.html (título)
  - apps/accounts/tests/test_auth_flow.py (ajuste de asserção)
  - docs/ + PROJECT_CONTEXT.md (nota da regra dos 3 tokens)
allowed_incidental_files:
  - .env.example (se citar o nome de exibição)
  - README.md (se citar o nome de exibição)
out_of_scope:
  - qualquer mudança visual além da marca (slices 002-005)
  - rename do ats-web (premissa externa, outro repositório)
  - rotas, views, FSM, guards (nada comporta muda)
```

## Objetivo

Todas as superfícies exibem `SAR – HMD` com a identidade reservada
intacta (primária `#0d4a8a`, `short_name "HMD"`, `theme_color`).

## Contexto necessário

- `config/settings/base.py:22` (`APP_DISPLAY_NAME`, default atual
  `HMD — Hemodinâmica`, override por env preservado) e
  `config/settings/test.py:60`; `apps/accounts/context_processors.py`
  expõe `app_display_name` a todos os templates.
- `static/manifest.json` (`name "Regulação - HMD"`, `short_name "HMD"`,
  `theme_color "#0d4a8a"`); `scripts/generate_pwa_icons.py` (texto
  `HMD` centralizado, `THEME_COLOR_RGB`, `--check` espelha o manifest).
- Decisões do design: D1 (3 tokens reservados), D6 (sigla + faixa
  geométrica de família, sem texto pequeno; variante `-contrast`),
  D9 (login mantém aviso transitório).

## Requisitos verificáveis

- **R1** — Default de `APP_DISPLAY_NAME` é `SAR – HMD` em `base` e
  `test`; override por `APP_DISPLAY_NAME` do ambiente preservado.
- **R2** — Manifest declara `name "SAR – HMD"`, `short_name "HMD"`,
  `theme_color "#0d4a8a"`.
- **R3** — SVGs exibem sigla `HMD` + faixa de família; variante
  `-contrast` existe; PNGs regenerados e `--check` passa; a
  regeneração é determinística (regenerar em diretório temporário
  produz bytes idênticos aos commitados).
- **R4** — Nenhuma superfície exibe o nome antigo; regra dos 3
  tokens registrada; `APP_DISPLAY_NAME` segue default sobrescrevível
  (teste cobre default + 1 override).
- **R5** — `sw.js` com `CACHE_NAME` v2 e precache incluindo os novos
  SVGs; bypass de rotas de PDF e de não-GET preservado.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `config/settings/*.py` | `pytest apps/accounts/tests/test_auth_flow.py` (asserção atualizada) |
| R2 | `static/manifest.json` | teste de manifest (novo, tipado) |
| R3 | `static/icons/*`, `scripts/generate_pwa_icons.py` | `--check` + teste de determinismo (bytes idênticos) |
| R5 | `static/js/sw.js` | teste de `CACHE_NAME` v2 + precache = manifest; bypass de PDF/POST preservado |
| R4 | templates/docs/testes | `grep -r "HMD — Hemodinâmica\|Regulação - HMD"` zerado nas superfícies + gate |

## Plano de testes

### RED

- Comando: `uv run pytest apps/accounts/tests/test_auth_flow.py -q`
  (com a asserção atualizada para `SAR – HMD` antes da mudança)
- Falha esperada: asserção do nome falha com o valor antigo.

### GREEN / verificação local

- `uv run pytest -q` — exit 0
- `uv run mypy .` — exit 0 (settings são cobertas pelo strict)
- `uv run ruff check . && uv run ruff format --check .` — exit 0

## Critérios de aceitação

- [ ] R1–R5 verdes; header, `<title>`, manual e PWA exibem `SAR – HMD`
- [ ] Primária, `short_name` e `theme-color` inalterados
- [ ] Nenhuma rota, view, guard ou FSM tocado

## Relatório de evidência

`/tmp/hmd-slice-001-report.md` (`READY_FOR_REVIEW` ou
`BLOCKED_NEEDS_DECISION`, com requirements→evidence, arquivos,
failing-before/passing-after e comandos com exit status).

## Handoff

Implementar apenas este slice. Escalar (`BLOCKED_NEEDS_DECISION`) se
algum teste ou doc exigir decisão sobre o nome além do aprovado
(incluindo qualquer pedido de mudar rotas/URLs). Não iniciar o slice 002.
