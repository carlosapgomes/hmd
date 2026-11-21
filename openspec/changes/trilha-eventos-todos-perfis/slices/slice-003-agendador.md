# Slice 003 — Trilha no detalhe do agendador

```yaml
expected_files:
  - apps/scheduler/presenters.py
  - templates/scheduler/case_detail.html
  - apps/scheduler/tests/test_views.py
```

Depende de: slice 001 (builder + partial em `apps/cases`).

## Contexto necessário

- Builder: `apps/cases/event_trail.py::build_event_trail_context` (slice 001).
- Agendador: `apps/scheduler/presenters.py::build_scheduler_case_detail_context`
  monta o contexto (visão limitada: one-liner re-identificado, decisões,
  agendamento, comunicações); `templates/scheduler/case_detail.html` termina
  nos cards de documento/texto (gated por `can_view_pdf`).
- Spec delta: `specs/scheduling/spec.md` deste change (cenário «Trilha do
  agendador mostra quem decidiu e quem criou»).
- Guard: `role_required("scheduler", "admin")` na view — intocado.

## Goal

Detalhe do agendador exibe a trilha ao final (collapsible fechado por padrão)
sem ampliar o acesso a artefatos clínicos do pipeline.

## Deliverables

### R1 — Contexto + template

- `build_scheduler_case_detail_context` monta `"event_trail"`.
- `templates/scheduler/case_detail.html`: `{% include
  "cases/_event_trail.html" %}` após o último card, antes de `extra_js` —
  fora dos gates de PDF/texto (a trilha não é artefato do pipeline).

### R2 — Testes (RED → GREEN)

- RED: detalhe do agendador renderiza «Trilha de eventos» fechado por padrão;
  ao abrir, eventos de criação (ator NIR) e decisão médica (ator médico) com
  papel pt-BR e data/hora; nenhuma linha adicional do sumário aparece
  (one-liner preservado); estrutura/alertas/sugestão seguem ausentes.
- Bateria completa do AGENTS.md + suíte.

## Gates para o reviewer (2 linhas)

1. Trilha renderizada fora dos gates `can_view_pdf`/texto (presente em caso
   pré-decisão também — é histórico do caso, não artefato clínico).
2. Nenhum conteúdo novo de pipeline além da trilha aparece para o agendador.

## Out of scope

- Filtros por papel; mudanças nos gates de PDF/texto; painel/NIR/médico.
