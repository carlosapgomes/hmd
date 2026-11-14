# Proposal: ui-parity-queues

## Why

Com a casca pronta (change 1), o trabalho diário do NIR, do médico e do
agendador ainda acontece em listas pobres: `list-group` sem hierarquia
visual, sem busca, sem auto-refresh, com paginação server-side a cada
navegação. No ats-web, as mesmas filas são `case-cards` com
`summary-box`, alerta de espera, busca client-side por nome/ocorrência
e atualização automática a cada 20 s. Este change traz as três filas
para a paridade de apresentação e interação — sem mudar regra de
negócio, ordenação, guards ou semântica das abas.

## What Changes

1. **Fila médica**: visual `case-card + summary-box + pulse-dot +
   waiting-time + status-badge`; abas `Pendentes/Decididos` (rótulo
   `Decididos` fiel à query por estado — **decisão B do dono**,
   encerra o débito do change 1; sem recorte de data); endpoint
   `doctor:queue_partial` + htmx a cada 20 s; busca client-side por
   nome/ocorrência; filtro por subtipo (server preservado como base,
   client como refinamento); badges de contagem nas abas.
2. **Fila do agendador**: mesmo padrão (abas `Pendentes/Processados`
   — rótulos já vigentes no `_nav` do change 1 — sem diferenciação por unidade, estados de
   reserva refletidos nos cards, `scheduler:queue_partial`).
3. **Meus casos (NIR)**: visual de cards; filtros server-side (busca
   por nº, status, tipo de procedimento) + partial com auto-refresh;
   abas `Ativos/Encerrados` preservadas com contadores; escopo por
   criador intacto.

## Capabilities

### New Capabilities

(nenhuma — a casca `ui-shell` já existe; este change estende a
apresentação das filas nos specs de domínio.)

### Modified Capabilities

- `doctor-decision`: fila médica ganha cards, partial com
  auto-refresh, busca client-side e rótulos `Pendentes/Decididos`
  (semântica por estado preservada).
- `scheduling`: fila do agendador ganha cards, partial com
  auto-refresh, busca client-side e rótulos no vocabulário ats-web.
- `intake-nir`: meus casos ganha filtros server-side, partial com
  auto-refresh e visual de cards (escopo por criador e abas
  preservados).

## Impact

- **Código**: `apps/doctor/views.py`, `apps/scheduler/views.py`
  (contexto de cards + endpoints partial), `apps/*/urls.py`
  (rotas `partials/queue/`), `templates/doctor/`,
  `templates/scheduler/`, `templates/intake/my_cases.html` +
  partials, `static/js/{doctor,scheduler,nir}_queue_filter.js`
  (novos, moldes ats-web adaptados), `.pi/skills/verify-hmd/`
  (extensão limitada: 3 jornadas).
- **Specs**: `doctor-decision`, `scheduling`, `intake-nir` (MODIFIED).
- **Comportamento preservado**: ordenação (tela desc + FIFO),
  guards por papel/subtipo, reservas (lock), paginação server-side,
  semântica das abas e `?tab=`/`?subtype=` atuais.
- **Fora de escopo** (change 3): upload, combobox, detalhe/decisão,
  painel.
