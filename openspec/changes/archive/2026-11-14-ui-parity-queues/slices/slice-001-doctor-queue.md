# Slice 001 — Fila médica em case-cards com partial e busca

```yaml
expected_files:
  - apps/doctor/views.py (contexto de cards + endpoint queue_partial)
  - apps/doctor/urls.py (rota partials/queue/)
  - templates/doctor/queue.html (abas Pendentes/Decididos + contadores + htmx)
  - templates/doctor/_queue_content.html (novo, fragmento compartilhado)
  - static/js/doctor_queue_filter.js (novo, molde ats-web adaptado)
  - apps/doctor/tests/test_*queue*.py (novos/estendidos, tipados)
  - .pi/skills/verify-hmd/{features,scripts}/ (jornada fila-medica-busca-refresh)
allowed_incidental_files: []
out_of_scope:
  - semântica das abas (decisão B: Decididos por estado, sem recorte de data)
  - ordenação, guards/subtipos, reservas, paginação, detalhe/decisão
  - fila do agendador e meus casos (slices 002-003)
```

## Objetivo

Fila médica no padrão ats-web: case-cards, abas com contadores, busca
client-side e auto-refresh de 20 s — com query, guards e ordenação
atuais intactos.

## Read first

- `AGENTS.md`, `PROJECT_CONTEXT.md`
- `openspec/changes/ui-parity-queues/proposal.md`, `design.md` (D1–D3, D5–D6), `specs/doctor-decision/spec.md`
- `templates/doctor/queue.html`, `templates/doctor/_nav.html` (change 1, mantido), `apps/doctor/views.py`, `apps/doctor/urls.py`
- Molde (somente leitura): `/projects/dev/ats-web/templates/doctor/_queue_content.html`, `/projects/dev/ats-web/static/js/doctor_queue_filter.js`

## Contexto necessário

- `apps/doctor/views.py` (fila por estado + `pending_counts`
  por subtipo), `templates/doctor/queue.html` (list-group atual),
  `templates/doctor/_case_cards.html` (cards do detalhe — padrão
  visual da casa, não copiar o ats-web byte a byte).
- Molde: `/projects/dev/ats-web/templates/doctor/{queue,_queue_content,
  _nav}.html` + `static/js/doctor_queue_filter.js` (filtro por exame,
  busca, status ARIA, reaplicação pós-swap).
- Decisões do design: D1 (partial = mesmo fragmento do server),
  D2 (paginação preservada), D3 (server autoritativo), D5
  (contadores sem N+1), D6 (`Decididos` fiel).

## Requisitos verificáveis

- **R1** — Cards `case-card` com nome+idade, nº, espera
  (`Aguardando há`/`Recebido há`), badge `N d em tela`, procedimentos
  + subtipo; o bloco legado `nav-tabs` (`Aguardando decisão/Decididos`)
é removido da página — a navegação passa a ser só o `_nav` pills do
change 1 (`Pendentes/Decididos` com badges de contagem, atualizados
na navegação, não no polling — D8).
- **R2** — `GET doctor:queue_partial` retorna o mesmo fragmento sob
  os mesmos guards; htmx atualiza a cada 20 s reaplicando busca e
  filtro ativos; caso fora do filtro/subtipo ou da página não aparece.
- **R3** — Busca client-side por nome/ocorrência filtra os cards sem
  requisição; `?subtype=` server-side inalterado (client só refina).
- **R4** — Estados de reserva refletidos (Avaliar / Continuar /
  Reservado + portador); expiração de leases antes da montagem
  (efeito colateral preexistente, igual na página e no partial — D9);
  ordenação tela-desc + FIFO e paginação preservadas.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `queue.html` (remove `nav-tabs`), `_queue_content.html` (novo) | testes de marcadores/classes por aba + ausência do bloco legado |
| R2 | `views.py`, `urls.py`, `_queue_content.html` | teste do fragmento (guards + conteúdo) + jornada harness |
| R3 | `doctor_queue_filter.js`, views | testes server + jornada (busca sem reload) + `assertNumQueries` |
| R4 | `views.py`, `_queue_content.html` | testes de lock (incl. via partial)/ordem/paginação existentes + novos |

## Plano de testes

### RED

- Comando: `uv run pytest apps/doctor/tests/ -q` (com os testes novos
  de marcadores/fragmento antes da implementação)
- Falha esperada: classes/fragmento/endpoint ausentes no HTML atual.

### GREEN / verificação local

- `uv run pytest apps/doctor -q`, `mypy`, `ruff check/format` — exit 0
- jornada verify-hmd: busca filtra sem reload; novo caso compatível com aba/filtro/página aparece em até ~20 s (caso fora do filtro ou da página não aparece); capturas vs ats-web

## Critérios de aceitação

- [ ] R1–R4 verdes; nenhum `?tab=`/`?subtype=` renomeado
- [ ] Filtro client nunca lista além do escopo server (teste com
  subtipo restrito)
- [ ] Sem N+1 novo; reservas e 403 intactos

## Relatório de evidência

`/tmp/hmd-slice-q001-report.md` (`READY_FOR_REVIEW` ou
`BLOCKED_NEEDS_DECISION`, com requirements→evidence, arquivos,
failing-before/passing-after e comandos com exit status).

## Handoff

Implementar apenas este slice. Escalar se o partial exigir query
nova cara, se algum contador não for computável sob os guards atuais,
ou se a semântica `Decididos` precisar mudar (fora do aprovado).
Não iniciar o slice 002.
