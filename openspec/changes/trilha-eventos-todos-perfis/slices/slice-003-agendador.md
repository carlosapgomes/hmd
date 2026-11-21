# Slice 003 — Trilha no detalhe do agendador

```yaml
expected_files:
  - apps/scheduler/presenters.py
  - templates/scheduler/case_detail.html
  - apps/scheduler/tests/test_views.py
  - apps/pipeline/tests/test_procedure_review_recovery.py
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

### R3 — Anti-drift da decisão B (P2 deferido do slice 002)

- `apps/pipeline/tests/test_procedure_review_recovery.py`, teste
  `test_dispatch_failure_preserves_confirmation_and_schedules_recovery`: o
  assert positivo passa a fixar também um fragmento distintivo do **valor
  enviado** da justificativa (ex.: `"Confirmo apos leitura"`) em
  `follow.content` — hoje pina só o rótulo `«Justificativa:»` e passaria
  mesmo se o texto livre deixasse de ser exibido (decisão B exige o texto).
- **Sem RED exigido**: comportamento já implementado e verde no slice 002 —
  é aperto de pin anti-drift, não comportamento novo (não fabricar RED; o
  AGENTS §5 veda RED falso). Rodar o teste alvo + suíte focada verde basta.

## Gates para o reviewer (3 linhas)

1. Trilha renderizada fora dos gates `can_view_pdf`/texto (presente em caso
   pré-decisão também — é histórico do caso, não artefato clínico).
2. Nenhum conteúdo novo de pipeline além da trilha aparece para o agendador.
3. Forma EXECUTÁVEL do gate de presença (correção do P2 do slice 002):
   `grep -rn '_event_trail.html' templates/scheduler` retorna o include; o
   título «Trilha de eventos» vive no partial — nunca grep do título nos
   templates que incluem.

## Out of scope

- Filtros por papel; mudanças nos gates de PDF/texto; painel/NIR/médico.
