# Slice 004: PWA — ícones HMD, manifest, service worker, wiring base

## Objetivo

App instalável: `manifest.json` (nome/short_name HMD), ícones base+maskable
com as letras **HMD** (SVG hand-written + PNGs gerados por script commitado
via PyMuPDF), service worker conservador (cache estáticos, network-first
HTML, bypass PDF, nunca POST) registrado no `base.html`.

## Contexto necessário

- Design D4 (`openspec/changes/dashboard-notifications-pwa/design.md`).
- ats-web SOMENTE-LEITURA (referência exata): `static/manifest.json`
  (estrutura de campos/ícones), `static/js/sw.js` (89 linhas — cache name
  versionado, pre-cache STATIC_ASSETS, activate limpando caches antigos,
  fetch GET-only com network-first para `/static/` e navegação,
  **bypass** de rotas de PDF antes do respondWith, network-only default),
  `templates/base.html` (link manifest + theme-color).
- Cor tema do HMD: `static/css/` (grep da cor do header/`.app-header`/
  `:root`) — use a MESMA cor do app em manifest `theme_color` e no fundo
  do ícone; `background_color` branco.
- HMD rota de PDF: `scheduler:case_pdf` — o path contém `/pdf/` (ver
  `apps/scheduler/urls.py`) → o bypass `url.pathname.includes("/pdf/")`
  do ats-web já cobre.
- PyMuPDF (fitz) já é dependência (pdf_utils) — o script de ícones:
  `fitz` cria página quadrada, desenha retângulo arredondado na cor tema
  com texto "HMD" centralizado (fonte Helvetica-Bold), exporta PNG nos
  tamanhos 72/96/128/144/152/192/384/512 + maskable 192/512 (com safe
  zone — conteúdo em ~80% central). Arquivos GERADOS são commitados
  (reprodutíveis pelo script; documentar no README do script).
- SW registration: snippet inline no fim do `<body>` do `base.html`
  (`navigator.serviceWorker.register('/static/js/sw.js')` no `load`,
  guardado por `'serviceWorker' in navigator`).

## Requisitos verificáveis

- **R1** `scripts/generate_pwa_icons.py` (idempotente, `uv run python
  scripts/generate_pwa_icons.py`): gera `static/icons/hmd-base.svg` NÃO
  (SVG é hand-written, commitado) — o script gera apenas os PNGs a partir
  do desenho em fitz (mesma geometria do SVG): `icon-{72..512}.png` e
  `maskable-{192,512}.png`; `--check` mode que falha se algum PNG do
  manifest estiver ausente do diretório (CI-friendliness).
- **R2** `static/icons/hmd-base.svg` + `hmd-maskable.svg`: letras "HMD"
  sobre fundo na cor tema (maskable com safe zone); **sem** "CHD" em
  lugar nenhum.
- **R3** `static/manifest.json`: name "Regulação - HMD", short_name
  "HMD", description do HMD, start_url "/", display standalone,
  theme_color cor do app, background_color #ffffff, icons apontando para
  SVGs (any) + PNGs (com sizes/type) + maskables — todos os arquivos
  listados EXISTEM (teste).
- **R4** `static/js/sw.js` adaptado: `CACHE_NAME = "hmd-cache-v1"`,
  STATIC_ASSETS com manifest/css/js/ícones do HMD; install pre-cache;
  activate limpa versões antigas + `clients.claim()`; fetch: não-GET
  retorna direto; `/static/` network-first com fallback cache; navegação
  HTML network-first com fallback `caches.match("/")` e **bypass
  `/pdf/`**; resto network-only. Sem referências a CHD/ats.
- **R5** `base.html`: `<link rel="manifest" href="/static/manifest.json">`
  + `<meta name="theme-color" content="{cor}">` no `<head>`; snippet de
  registro do SW no fim do body.
- **R6** Testes (smoke, sem browser): GET `/static/manifest.json` 200 com
  `"HMD"` e sem `"CHD"`; todos os ícones do manifest servem 200
  (parse do JSON e requisição de cada src); GET `/static/js/sw.js` 200 e
  conteúdo sem "CHD"/"ats-cache"; página autenticada renderizada contém
  `rel="manifest"`, `theme-color` e `serviceWorker.register`; script
  `--check` passa com os PNGs commitados.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `scripts/generate_pwa_icons.py` | `test_icon_script_check_passes` (chama `--check`) |
| R2 | `static/icons/hmd-base.svg`, `static/icons/hmd-maskable.svg` | smoke no teste de manifest |
| R3 | `static/manifest.json` | `test_manifest_served_hmd_no_chd`, `test_manifest_icons_exist` |
| R4 | `static/js/sw.js` | `test_sw_served_no_ats_refs` |
| R5 | `templates/base.html` | `test_base_declares_pwa` |
| R6 | `apps/accounts/tests/test_pwa.py` (ou `apps/dashboard/tests/` — escolha o mais natural e registre) | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - scripts/generate_pwa_icons.py
  - static/manifest.json
  - static/js/sw.js
  - static/icons/hmd-base.svg
  - static/icons/hmd-maskable.svg
  - static/icons/icon-{72,96,128,144,152,192,384,512}.png   # gerados, commitados
  - static/icons/maskable-{192,512}.png
  - templates/base.html        # manifest + theme-color + registro SW
  - apps/<app>/tests/test_pwa.py

out_of_scope:
  - notificações/dashboard (outras slices); push notifications; offline de dados
  - ícone favicon (já existe? manter como está); iOS meta específicas (apple-touch — opcional, só se trivial)
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_pwa.py`
- Falha esperada: 404 em `/static/manifest.json` / arquivo de teste novo sem assets.

### GREEN / verificação local

- `uv run python scripts/generate_pwa_icons.py --check` — exit 0
- `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/` — exit 0
- `uv run ruff check scripts apps && uv run ruff format --check scripts apps`
- `uv run mypy .` (script incluído se estiver no escopo do mypy)

## Critérios de aceitação

- [ ] R1–R6 comprovados; zero referências a CHD/ats em manifest/sw/ícones
- [ ] Todos os ícones do manifest existem de fato (teste itera o JSON)
- [ ] SW nunca intercepta POST nem rotas de PDF (código espelha ats-web
      nesses guards, com nomes/cache do HMD)
- [ ] Gate parcial do slice verde
