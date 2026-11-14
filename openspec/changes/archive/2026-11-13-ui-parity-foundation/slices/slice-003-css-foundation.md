# Slice 003 — Fundação CSS + títulos de página

```yaml
expected_files:
  - static/css/app.css
  - templates/base.html (só blocos <head>: fontes + CSS)
  - templates/accounts/{home,intranet_blocked,login,manual,notifications,profile,switch_role}.html (só h1.page-title)
  - templates/dashboard/{admin_close_confirm,case_detail,home}.html (só h1.page-title)
  - templates/doctor/{case_detail,decide,queue}.html (só h1.page-title)
  - templates/intake/{case_detail,corrected_resubmission,home,my_cases}.html (só h1.page-title)
  - templates/scheduler/{case_detail,queue}.html (só h1.page-title)
  - apps/*/tests/test_*_titles.py (novos, tipados) ou extensão dos testes de view existentes
allowed_incidental_files: []
out_of_scope:
  - header/nav/sino (slice 004), páginas de sessão além do h1 (slice 005)
  - .case-card/.summary-box nas filas (visual das filas é do change 2; o CSS pode incluir os seletores, sem aplicá-los)
  - upload/combobox (change 3)
```

## Objetivo

Fundação visual servida e cada página com exatamente um `h1.page-title`
com o nome da visão.

## Contexto necessário

- `static/css/app.css` atual (~150 linhas, tokens `--hmd-*`, sem
  `.page-title`, sem componentes, sem touch-targets, sem responsivo
  detalhado) e o molde `/projects/dev/ats-web/static/css/app.css`
  (tokens `--hospital-*`, Merriweather Sans + Source Sans 3, `.page-title`,
  botões/badges/status, `min-height` de toque, media queries).
- `templates/base.html` (bloco `{% block page_title %}` ainda não
  existe — o ats-web o tem; páginas HMD usam `h1.h3/h4` ad-hoc).
- Decisões do design: D1 (primária preservada), D2 (nomes
  `--hospital-*`), D3 (adaptação, não cópia byte a byte).
- Harness do slice 002 disponível para capturas comparativas.

## Requisitos verificáveis

- **R1** — `app.css` serve fontes, tokens `--hospital-*` (primária
  `#0d4a8a`), `.page-title`, botões/badges/status/`waiting-time`,
  alvos ≥44px e responsivo; sem referência restante a `--hmd-*`.
- **R2** — Toda página listada renderiza exatamente um `h1.page-title`
  com o nome da visão, distinto do `<title>`.
- **R3** — Nenhuma rota, view, guard ou texto de regra de negócio muda
  (só `h1`/classes); controles primários com altura ≥44px e fontes
  do tema conferidos no browser (viewports 360px e 1280px).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `static/css/app.css`, `base.html` head | `pytest` (seletores servidos) + captura vs ats-web |
| R2 | templates listados | testes de `h1.page-title` por rota (RED antes) |
| R3 | — | suíte existente verde sem alteração de comportamento |

## Plano de testes

### RED

- Comando: `uv run pytest <novos testes de título> -q`
- Falha esperada: `h1.page-title` ausente nos HTMLs atuais.

### GREEN / verificação local

- `uv run pytest -q`, `mypy`, `ruff check/format` — exit 0
- drive do harness nas páginas-alvo: família de fontes computada =
  tema, altura dos controles primários ≥44px, capturas 360px/1280px
  vs ats-web para revisão do dono

## Critérios de aceitação

- [ ] R1–R3 verdes; um `h1` por página, sem duplicidade com marca
- [ ] CSS sem `--hmd-*`; primária e `theme-color` intactos
- [ ] Nenhum teste de comportamento ajustado para "passar" (só os novos de título)

## Relatório de evidência

`/tmp/hmd-slice-003-report.md` (requirements→evidence, arquivos,
failing-before/passing-after, comandos com exit status, capturas).

## Handoff

Implementar apenas este slice. Escalar se algum template exigir
contexto novo da view para o título (decisão de backend fora do
aprovado) ou se Google Fonts exigir infra além da tag (não deve).
Não iniciar o slice 004.
