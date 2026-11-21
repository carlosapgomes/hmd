# Design: trilha-eventos-todos-perfis

## Context

A trilha de eventos é o `CaseEvent` append-only (ator/papel/timestamp/payload
enxuto). Hoje ela é renderizada apenas no detalhe do painel (manager/admin):
contexto montado em `apps/dashboard/views.py::_case_detail_context` com
`select_related("actor")`, rótulos/badges de `apps/dashboard/event_labels.py`
(cobertura 45/45 com teste anti-drift) e detalhes de
`apps/cases/procedure_review.py::event_detail_lines` (só eventos de revisão —
payload bruto nunca é renderizado). O markup é card collapsible «Trilha de
eventos» fechado por padrão (`templates/dashboard/case_detail.html`).

A remoção da trilha do NIR/médico (`painel-ats-parity` D4, decisão do dono de
2026-09-15) teve como motivo o jargão técnico. Desde então a trilha ganhou
rótulos legíveis completos, badges por papel, papéis em português
(`role_label`) e linhas de detalhe — e o dono reverte a decisão por paridade
(proposal «Why»). Detalhe do agendador nunca teve trilha.

Constraint de arquitetura relevante: dependências unidirecionais entre apps —
`dashboard` importa de `cases` (e de todos), nunca o contrário; `intake`,
`doctor` e `scheduler` não importam de `dashboard` hoje. Templates são
centralizados em `templates/<app>/` com partials `_*.html` por app.

## Goals / Non-Goals

**Goals:**

- Um único componente de trilha (builder + partial) renderizado em TODAS as
  superfícies de detalhe: painel (mantido), NIR, médico e agendador — paridade
  de auditoria por construção (mesma fonte, mesmo markup).
- Trilha idêntica em conteúdo para todos os papéis — quem fez o quê, quando,
  com rótulo legível e badge por categoria; card fechado por padrão no final
  da página.
- Preservar o comportamento vigente do painel sem regressão de markup/testes.

**Non-Goals:**

- Filtrar categorias de evento por papel (locks/anonimização aparecem para
  todos — decisão de paridade; o card fechado mitiga o ruído).
- Expor motivo técnico de falha ao NIR (fora da renderização — só
  `event_detail_lines` projeta payload, e só para eventos de revisão).
- Novos modelos, rotas, guards, migrações ou dependências.
- Trilha em filas/listas, notificações ou PWA.
- Touch no `decision_event` do médico como requisito (derivação é refator
  interno opcional coberto por teste existente).

## Decisions

### D1 — Casa do componente: `apps/cases/event_trail.py`

`EVENT_LABELS`, `EVENT_BADGE_CSS`, `event_label`, `event_badge_css` **migram**
de `apps/dashboard/event_labels.py` para o novo `apps/cases/event_trail.py`,
junto com o novo `build_event_trail_context(case) -> list[dict]` (label,
badge_css, actor_display com fallback «Sistema», actor_role, timestamp
localizado, details via `event_detail_lines`) sobre `case.events
.select_related("actor")` NA ORDEM DO QUERYSET (`CaseEvent.Meta.ordering`:
timestamp, id — cronológica ascendente, paridade com o painel). `cases` é o
dono do domínio
`CaseEvent` e já exporta `event_detail_lines` consumido pelo dashboard
(precedente de direção). O dashboard passa a importar de `cases`.

Alternativa rejeitada: manter em `apps/dashboard` e fazer
intake/doctor/scheduler importarem de lá — inverteria a direção de
dependência (apps operacionais → painel) e acoplaria fluxos ao dashboard.

### D2 — Partial único `templates/cases/_event_trail.html`

Markup extraído 1:1 do card do painel (mesmas classes, `data-bs-toggle`,
`aria-expanded="false"`, sem `show`, botão «Mostrar trilha»), iterando
`event_trail`. O template do painel substitui o bloco inline pelo `{% include
"cases/_event_trail.html" %}` — paridade por construção. Pasta
`templates/cases/` é nova (o app `cases` não tinha UI), coerente com o padrão
`templates/<app>/_<partial>.html` dos demais apps.

Context key padronizada: `event_trail` em todas as superfícies (o dashboard
troca `events`→`event_trail`; testes que fatiam o HTML por «Trilha de
eventos» seguem válidos pois o markup não muda).

### D3 — Montagem por superfície (builder puro, sem N+1)

- **Painel**: `_case_detail_context` usa `build_event_trail_context(case)`
  (substitui a lista inline).
- **NIR**: `apps/intake/views.py::case_detail` monta `event_trail` no
  contexto (queryset já existente; +1 query `select_related("actor")`).
- **Médico**: `apps/doctor/presenters.py::build_case_detail_context` monta
  `event_trail`; `decision_event` passa a ser derivado da lista carregada
  (evento `CASE_DOCTOR_DECISIONS_RECORDED` mais recente) — remove a query
  dedicada, comportamento idêntico, testes existentes cobrem o card.
- **Agendador**: `apps/scheduler/presenters.py::build_scheduler_case_detail_context`
  monta `event_trail`.

Posição: partial incluído após o último card existente de cada detalhe
(«no final»), antes dos blocks `extra_js`.

### D4 — Trilha integral, sem filtro por papel

Todos os papéis veem a mesma trilha (45 tipos), incluindo eventos técnicos
(locks, anonimização, dispatch de OCR). Justificativa: paridade de auditoria
— trilhas divergentes por papel criam ambiguidade de «versão oficial» e
multiplicam testes. O ruído é contido pelo card fechado por padrão. Badge de
erro do NIR em `FAILED` é mantido (atalho para o estado sem abrir a trilha).

### D5 — Specs: títulos verbatim, corpos governam (quirk D5b)

Os cenários «Detalhe em decisão sem trilha de eventos» (doctor-decision) e
«Detalhe exibe documentos e trilha» (intake-nir) mantêm os títulos verbatim —
o validador exige; os corpos governam o comportamento novo (trilha
presente). Custo conhecido e documentado do formato (precedente
`painel-ats-parity` D5b). O delta introduz três cenários novos de autoria
(«Trilha do NIR/médico/agendador mostra quem fez o quê»).

### D6 — Testes: ausência → presença, cobertura nova no scheduler

Os asserts anti-drift de ausência («Trilha de eventos» not in body) viram
asserts de presença com as propriedades contratuais: título presente, card
fechado por padrão (sem classe `show` no collapse; `aria-expanded="false"`),
botão `data-bs-toggle="collapse"`, rótulo legível + ator + papel pt-BR +
data/hora. `apps/dashboard/tests/test_event_labels.py` migra para
`apps/cases/tests/` (anti-drift do enum continua iterando `CaseEventType`).
Testes de renderização do painel seguem com as asserções INTACTAS (prova da
extração 1:1) — os únicos toques permitidos nesses arquivos são imports do
caminho novo do módulo de labels (`test_views.py` importa `EVENT_LABELS`;
`apps/cases/tests/test_purge_closed_case_data.py` importa `event_label`).
`test_role_labels.py` reescrito para as superfícies novas (spec `account-access`
também recebe delta: cenários de rótulo na trilha deixam de restringir ao
painel — sobreposição intencional com a reversão do `painel-ats-parity`).

## Risks / Trade-offs

- [Jargão técnico exposto a papéis clínicos (locks/anonimização/OCR)] →
  Mitigado: rótulos legíveis + badge por categoria + card fechado por padrão;
  se o dono pedir depois, filtro por papel é change nova (não prevista).
- [Scheduler, cujo detalhe é PHI-limitado, passa a ver nomes de colegas e
  eventos operacionais] → Aceito pelo dono (paridade de auditoria); não é PHI
  do paciente; atores são funcionários internos.
- [Regressão de markup/testes do painel na extração do partial] → Extração
  1:1 + suíte do dashboard verde como gate do slice 001: asserções de
  renderização inalteradas; apenas imports de teste apontam o caminho novo
  (`test_views.py`, `test_purge_closed_case_data.py`).
- [Volume de eventos por caso (30–60) em cada detalhe] → +1 query com
  `select_related("actor")`, renderização só no DOM (collapsible Bootstrap,
  sem JS novo); custo desprezível frente à paginação das filas.
- [Confusão contratual do cenário «sem trilha» com título preservado] →
  Documentado aqui (D5) e no delta; corpos governam.
- [Spec `account-access` pinava «detalhes de NIR e médico não exibem mais
  trilha»] → Delta próprio neste change (cenários de rótulo estendidos às
  novas superfícies) — sem ele, specs ficariam contraditórias.

## Migration Plan

Sem migrações de dados, feature flags ou deploy especial. Rollback = revert
dos slices (a trilha some das superfícies novas; nada persistido). Os slices
não quebram o sistema entre si: 001 é autocontido (painel continua íntegro),
002/003 adicionam superfícies independentes.

## Open Questions

(nenhuma — decisões de produto tomadas pelo dono nesta proposta; detalhes
restantes são refactors internos cobertos por testes)
