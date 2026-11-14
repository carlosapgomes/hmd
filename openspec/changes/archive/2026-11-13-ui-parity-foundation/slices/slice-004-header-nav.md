# Slice 004 — Header, navegação por papel e sino com polling

```yaml
expected_files:
  - templates/base.html (header, blocos nav/page_title, htmx, favicon, PWA)
  - templates/intake/_nav.html (novo)
  - templates/doctor/_nav.html (novo)
  - templates/scheduler/_nav.html (novo)
  - templates/dashboard/_nav.html (novo, se o painel tiver abas próprias)
  - templates que passam a declarar {% block nav %} (filas, envio, meus casos, painel, notificações)
  - static/js/bell_poll.js (novo, vanilla; molde notifications.js do ats-web adaptado às rotas HMD)
  - apps/accounts/context_processors.py (+ contadores das abas, se inexistentes) e testes de contexto
  - .pi/skills/verify-hmd/{features,scripts}/ (extensão limitada: jornada header-nav-4-papeis)
allowed_incidental_files: []
out_of_scope:
  - visual das filas/meus casos além de nav+h1 (change 2)
  - leitura individual de notificação (slice 005)
  - partials htmx de fila (change 2; aqui só a tag script como fundação)
```

## Objetivo

Header padrão ats-web (ícone + `SAR – HMD`, sessão `Nome · Papel`,
sino com polling, `?` manual, toggler) com faixa `app-nav` por papel
e abas no vocabulário do ats-web.

## Contexto necessário

- `templates/base.html` atual (links de papel soltos, sino SSR, sem
  `{% block nav %}` consumido) e o molde
  `/projects/dev/ats-web/templates/base.html` (marca com logo 32px,
  sessão desktop/mobile, `{% block nav %}`, htmx 2.0.4, `app.js`).
- Parciais molde: `/projects/dev/ats-web/templates/{doctor,scheduler,
  intake}/_nav.html` (`nav-pills`, aba ativa, `data-count`).
- Endpoint `notifications/unread-count/` já existe
  (`apps/accounts/urls.py`); sino do ats-web usa `data-*` + polling.
- Mapeamento de rótulos (decisão do dono): abas exibem os termos do
  ats-web; valores internos `?tab=` (ex. `aguardando/decididos`)
  preservados (D4 do design).
- Contadores das abas: reaproveitar o que as views já calculam
  (ex. `_pending_counts_by_subtype` no doctor); se faltar total para
  a badge, estender o context processor com teste.

## Requisitos verificáveis

- **R1** — Header exibe ícone 32px + `SAR – HMD`, sessão
  `Nome · Papel` + avatar + sino + manual + sair (+toggler mobile);
  `htmx 2.0.4`, favicon e PWA declarados.
- **R2** — Cada papel tem `_nav.html` com abas no vocabulário ats-web
  (`Decididos`, sem recorte de data — débito do change 2), aba corrente
destacada e contadores; páginas declaram `{% block nav %}`. Tabela de
mapeamento no relatório: papel → aba → rota → `?tab=` → regra de ativo
→ contador (origem, escopo e custo); inclui admin, páginas fora da
área do papel e o caso do painel sem abas próprias. Contadores novos
só via queries leves sob os mesmos guards das views.
- **R3** — Sino atualiza a contagem via polling no endpoint existente
  sem reload (45 s default, backoff até 5 min, pausa em aba oculta,
  poll imediato ao voltar); sem htmx envolvido no sino (D5).
  Cobertura desktop + mobile (sino mobile e sino desktop).
- **R4** — Rotas, guards, `?tab=` e comportamentos intactos.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `base.html` | testes de marcadores por papel (RED antes) |
| R2 | `_nav.html` + plugs | testes de aba ativa por rota |
| R3 | `bell_poll.js`, endpoint | teste de endpoint + presença `data-*` + jornada (zero→N sem reload) desktop e mobile |
| R4 | — | suíte existente verde |

## Plano de testes

### RED

- Comando: `uv run pytest <novos testes de header/nav> -q`
- Falha esperada: parciais/blocos/marcadores ausentes no HTML atual.

### GREEN / verificação local

- `uv run pytest -q`, `mypy`, `ruff check/format` — exit 0
- jornada verify-hmd: login nos 4 papéis → captura header+nav de cada um

## Critérios de aceitação

- [ ] R1–R4 verdes; sino reflete nova notificação em até 60 s sem reload
- [ ] Nenhum `?tab=` renomeado; nenhum guard/redirect alterado
- [ ] Mobile: toggler abre sessão sem overflow (captura estreita)

## Relatório de evidência

`/tmp/hmd-slice-004-report.md` (requirements→evidence, arquivos,
failing-before/passing-after, comandos com exit status, capturas por papel).

## Handoff

Implementar apenas este slice. Restrição: NÃO implementar recorte de
data na aba de decididos (débito registrado do change 2). Escalar se
alguma aba exigir contagem que a view não calcula hoje com custo
relevante, ou se o polling exigir mudança no endpoint (fora do
aprovado). Não iniciar o slice 005.
