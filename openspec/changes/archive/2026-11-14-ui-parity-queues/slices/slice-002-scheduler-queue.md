# Slice 002 — Fila do agendador em case-cards com partial e busca

```yaml
expected_files:
  - apps/scheduler/views.py (contexto de cards + endpoint queue_partial)
  - apps/scheduler/urls.py (rota partials/queue/)
  - templates/scheduler/queue.html (abas + contadores + htmx)
  - templates/scheduler/_queue_content.html (novo, fragmento compartilhado)
  - static/js/scheduler_queue_filter.js (novo, molde ats-web adaptado)
  - apps/scheduler/tests/test_*queue*.py (novos/estendidos, tipados)
  - apps/scheduler/tests/test_views.py (asserts existentes da fila que a remoção do legado pode afetar)
  - .pi/skills/verify-hmd/{features,scripts}/ (jornada fila-agendamento)
allowed_incidental_files: []
out_of_scope:
  - diferenciação por unidade na fila (proibida pela spec), confirmação/negação/intercorrência
  - ordenação, guards, reservas, paginação
  - fila médica (slice 001) e meus casos (slice 003)
```

## Objetivo

Fila do agendador no padrão ats-web, com os estados de reserva
refletidos nos cards e atualização automática — sem mudar query,
guards ou paginação.

## Read first

- `AGENTS.md`, `PROJECT_CONTEXT.md`
- `openspec/changes/ui-parity-queues/proposal.md`, `design.md` (D1–D2, D5–D6), `specs/scheduling/spec.md`
- `templates/scheduler/queue.html`, `templates/scheduler/_nav.html` (change 1, mantido), `apps/scheduler/views.py`, `apps/scheduler/urls.py`
- Molde (somente leitura): `/projects/dev/ats-web/templates/scheduler/_queue_content.html`, `/projects/dev/ats-web/static/js/scheduler_queue_filter.js`
- Slice 001 como referência de padrão (partial, htmx 20 s, `assertNumQueries`)

## Contexto necessário

- `apps/scheduler/views.py` (fila por abas aguardando/processados),
  `templates/scheduler/queue.html` (list-group atual).
- Molde: `/projects/dev/ats-web/templates/scheduler/{queue,
  _queue_content}.html` + `static/js/scheduler_queue_filter.js`.
- Slice 001 como referência de padrão (partial, htmx 20 s,
  reaplicação de filtro, `assertNumQueries`).
- Decisões do design: D1, D2, D5–D9 (`Pendentes/Processados`; badges
  na navegação; expiração de leases igual no partial).

## Requisitos verificáveis

- **R1** — Cards `case-card` com nome+idade, nº, espera, badge de
  tela, procedimentos, `unit_label` quando houver; o bloco legado
  `nav-tabs` é removido da página — a navegação passa a ser só o
  `_nav` pills do change 1 (`Pendentes/Processados` com contadores,
  atualizados na navegação — D8).
- **R2** — `GET scheduler:queue_partial` sob os mesmos guards; htmx
  20 s com reaplicação da busca; caso posicionado em outra página
  não aparece.
- **R3** — Busca client-side por nome/ocorrência sem requisição.
- **R4** — Reserva refletida (abrir / Continuar / Reservado +
  portador), inclusive via partial (expiração igual à página — D9);
  sem diferenciação por unidade; 403 e paginação intactos.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `queue.html` (remove `nav-tabs`), `_queue_content.html` (novo) | testes de marcadores por aba + ausência do bloco legado |
| R2 | `views.py`, `urls.py`, `_queue_content.html` | teste do fragmento + jornada harness |
| R3 | `scheduler_queue_filter.js` | jornada (busca sem reload) |
| R4 | `views.py`, `_queue_content.html` | testes de lock (incl. via partial)/guards/paginação |

## Plano de testes

### RED

- Comando: `uv run pytest apps/scheduler/tests/ -q` (testes novos antes)
- Falha esperada: classes/fragmento/endpoint ausentes.

### GREEN / verificação local

- `uv run pytest apps/scheduler -q`, `mypy`, `ruff check/format` — exit 0
- jornada verify-hmd (novo caso compatível com aba/página aparece sem reload) + capturas vs ats-web

## Critérios de aceitação

- [ ] R1–R4 verdes; unidade nunca filtra a fila
- [ ] Reserva de outro agendador bloqueia sem ação; a própria oferece Continuar

## Relatório de evidência

`/tmp/hmd-slice-q002-report.md` (mesmo formato do slice 001).

## Handoff

Implementar apenas este slice. Escalar se contador ou partial
exigir query além dos guards atuais. Não iniciar o slice 003.
