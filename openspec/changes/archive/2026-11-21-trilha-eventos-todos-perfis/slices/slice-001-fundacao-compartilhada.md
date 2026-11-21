# Slice 001 — Fundação compartilhada da trilha (apps/cases + partial + painel)

```yaml
expected_files:
  - apps/cases/event_trail.py
  - apps/dashboard/event_labels.py
  - apps/dashboard/views.py
  - templates/cases/_event_trail.html
  - templates/dashboard/case_detail.html
  - apps/cases/tests/test_event_trail.py
  - apps/dashboard/tests/test_event_labels.py
  - apps/dashboard/tests/test_views.py
  - apps/cases/tests/test_purge_closed_case_data.py
```

## Contexto necessário

- Trilha do painel hoje: contexto em `apps/dashboard/views.py::_case_detail_context`
  (lista `events` com `select_related("actor")`, `timezone.localtime`,
  `event_detail_lines`); markup do card em `templates/dashboard/case_detail.html`
  (~L243-272: collapsible `#case-event-trail`, sem `show`, botão «Mostrar trilha»).
- Rótulos/badges: `apps/dashboard/event_labels.py` (`EVENT_LABELS` 45/45,
  `EVENT_BADGE_CSS`, `event_label`, `event_badge_css`); teste anti-drift em
  `apps/dashboard/tests/test_event_labels.py` (itera o enum).
- Detalhes: `apps/cases/procedure_review.py::event_detail_lines` (fica onde está —
  já é `cases`).
- Precedente de direção: dashboard já importa `event_detail_lines` de
  `apps.cases` — D1 do design.

## Goal

Componente único de trilha em `apps/cases` (builder + partial) consumido pelo
painel sem mudança de comportamento visível — paridade por construção.

## Deliverables

### R1 — Módulo `apps/cases/event_trail.py`

- Mover (não copiar) `EVENT_LABELS`, `EVENT_BADGE_CSS`, `event_label`,
  `event_badge_css` (e constantes de badge) de `apps/dashboard/event_labels.py`;
  o módulo do dashboard é removido e os imports do dashboard apontam para
  `apps.cases.event_trail`.
- Novo `build_event_trail_context(case: Case) -> list[dict[str, object]]`:
  uma query `case.events.select_related("actor")` NA ordem do queryset
  (`CaseEvent.Meta.ordering` = timestamp, id — cronológica ascendente,
  paridade com o painel); por evento — `label`
  (`event_label`), `badge_css` (`event_badge_css` com payload), `actor_display`
  (`actor.display_name` ou «Sistema»), `actor_role`, `timestamp`
  (`timezone.localtime`), `details` (`event_detail_lines`). Puro, sem request.

### R2 — Partial `templates/cases/_event_trail.html`

- Markup extraído 1:1 do card do painel: header «Trilha de eventos» + botão
  «Mostrar trilha» (`data-bs-toggle="collapse"`, `aria-expanded="false"`,
  `aria-controls`), `div.collapse` SEM `show`, lista com badge
  `{{ event.badge_css }}`, ator + `{{ event.actor_role|role_label }}`,
  details e timestamp `d/m/Y H:i`; `{% empty %}` «Sem eventos registrados.».
- Itera `event_trail` (chave única do partial).

### R3 — Painel consome o componente

- `templates/dashboard/case_detail.html`: bloco inline da trilha substituído
  por `{% include "cases/_event_trail.html" %}` (posição idêntica — antes do
  card de encerramento administrativo).
- `_case_detail_context`: `"events"` → `"event_trail"` via
  `build_event_trail_context(case)`; docs do módulo atualizadas.
- `apps/dashboard/views.py` importa de `apps.cases.event_trail` — nenhum
  outro import de `apps.dashboard.event_labels` sobra no REPO INTEIRO
  (apps/, tests/, config/).

### R4 — Testes (RED → GREEN)

- RED: `apps/cases/tests/test_event_trail.py` — builder devolve eventos na
  ordem cronológica ascendente (timestamp, id — igual ao painel) com
  label/badge/ator(«Sistema» sem ator)/papel/timestamp localizado/
  details; teste anti-drift MIGRADO de `test_event_labels.py` (itera
  `CaseEventType`; módulo antigo removido).
- Dashboard verde com as ASSERÇÕES de renderização intactas (test_views.py,
  test_detail_cards.py, test_summary_card.py, test_attachment_section.py) —
  prova da extração 1:1; os únicos toques permitidos são IMPORTS de teste no
  caminho novo: `apps/dashboard/tests/test_views.py:54` (`EVENT_LABELS`) e
  `apps/cases/tests/test_purge_closed_case_data.py:48` (`event_label`).
- Bateria completa do AGENTS.md + suíte.

## Gates para o reviewer (2 linhas)

1. `grep -rn "dashboard.event_labels" apps/ tests/ config/` vazio;
   `apps/cases/event_trail.py` é a única casa de `EVENT_LABELS`.
2. Suíte do dashboard verde com asserções de renderização inalteradas (apenas
   imports de teste apontando o caminho novo).

## Out of scope

- Nir/médico/agendador (slices 002/003); mudar rótulos/badges existentes;
  qualquer mudança visual no card.
