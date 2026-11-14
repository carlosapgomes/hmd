# Design: ui-parity-queues

## Context

Ver proposal.md (Why). Estado atual: filas em `list-group` com
`nav-tabs` e paginação server-side, sem busca, sem auto-refresh, sem
contadores (o subtitle-filter da fila médica mostra contadores só de
subtipo). A casca do change 1 fornece `{% block nav %}`, `_nav.html`
por papel, htmx 2.0.4, sino com polling e o harness `verify-hmd`.
Moldes: `templates/doctor/{queue,_queue_content,_nav}.html`,
`templates/scheduler/{queue,_queue_content}.html`,
`templates/intake/{my_cases,_my_cases_content}.html` e
`static/js/{doctor,scheduler}_queue_filter.js` do ats-web.

## Goals / Non-Goals

- Goals: as três filas com apresentação e interação do ats-web,
  adaptadas às rotas/params/fields do HMD.
- Non-Goals: semântica das abas (decisão B do dono — `Decididos` por
  estado, sem recorte de data); ordenação, guards, reservas, FSM;
  upload, combobox, detalhe/decisão, painel (change 3).

## Decisions

- **D1 — Partials espelham a primeira renderização.** Cada fila ganha
  `partials/queue/` (médico, agendador) e `meus casos` ganha partial
  própria; a página cheia renderiza o mesmo fragmento no server-side
  (funciona sem JS; testes pytest cobrem o HTML). O htmx troca o
  `innerHTML` a cada 20 s, como no ats-web.
- **D2 — Paginação server-side preservada onde existe.** Médico e
  agendador: o partial inclui a paginação; links de página seguem
  navegação normal (fora do swap). Meus casos NÃO tem paginação
  (a view materializa a lista inteira) e continua sem — introduzi-la
  seria mudança de comportamento fora do escopo.
  Paginação client-side ou infinita: não.
- **D3 — Filtro server como base, client como refinamento.** Na fila
  médica, `?subtype=` continua autoritativo (é também access control);
  o JS só oculta cards do renderizado, nunca amplia escopo. Busca por
  nome/ocorrência é 100 % client-side sobre os cards da página —
  mesma divisão do ats-web.
- **D4 — Meus casos: filtros server-side novos.** `q` (nº de
  ocorrência, mínimo 3 caracteres, espelho do painel), `status`,
  `procedure_type`; composição AND; preservados entre página cheia e
  partial (não há paginação nesta view); abas `Ativos/Encerrados` e
  escopo por criador intactos. Os filtros aplicam-se antes do cálculo
  de sufixos de desambiguação, como já fazem as abas hoje.
- **D5 — Contadores sem query nova cara.** Badges das abas usam
  contagens que as views já calculam ou agregações leves sob os
  mesmos guards (sem `select_related` novo, sem N+1 — coberto por
  `assertNumQueries` onde existir padrão).
- **D6 — Rótulos fiéis (decisão B).** `Pendentes/Decididos`,
  `Pendentes/Processados` (rótulos já vigentes nos `_nav` do
  change 1; os blocos legados `nav-tabs` das páginas saem nos
  slices 001–002 para não duplicar a navegação), `Ativos/Encerrados`
  (pills próprias, mantidas e reestilizadas no slice 003). Nenhum
  rótulo promete recorte que a query não faz.
- **D7 — verify-hmd estendido de forma limitada.** 3 jornadas novas
  (`fila-medica-busca-refresh`, `fila-agendamento`,
  `meus-casos-filtros`); sem mudar launch/doctor.

## Risks / Trade-offs

- [Risco] Polling de 20 s com partial pesado → Mitigação: mesmo
  padrão do ats-web; partial reutiliza o queryset da página;
  monitorar `assertNumQueries`.
- [Risco] Filtro client dessincronizado do server após refresh →
  Mitigação: após cada swap, o JS reaplica busca/filtro ativos
  (mesmo padrão dos filtros do ats-web).
- [Risco] Cards de reserva (lock) renderizados no partial →
  Mitigação: expiração de leases continua server-side antes da
  montagem, como hoje; partial reflete o mesmo estado.
- **D8 — Badges contam na navegação, não no polling.** O `_nav`
  fica fora do fragmento trocado (mesmo layout do ats-web); as
  contagens atualizam a cada navegação completa, não a cada ciclo
  de 20 s. Sem update out-of-band (sem endpoint novo, sem escopo
  extra).
- **D9 — Partial reusa o assembly com o efeito colateral existente.**
  A expiração lazy de reservas escreve no banco — o partial executa
  o mesmo caminho da página cheia, com o mesmo custo e a mesma
  semântica (não é GET puro); locks são testados inclusive via
  partial.

## Migration Plan

Sem migrations. Deploy: templates + 3 JS + 3 rotas partial (mesmos
guards das páginas; o partial executa o assembly com o efeito
colateral preexistente de expiração de leases). Rollback: revert.
