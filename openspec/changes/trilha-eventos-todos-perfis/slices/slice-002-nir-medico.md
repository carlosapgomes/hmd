# Slice 002 — Trilha no detalhe do NIR e do médico

```yaml
expected_files:
  - apps/intake/views.py
  - apps/doctor/presenters.py
  - templates/intake/case_detail.html
  - templates/doctor/case_detail.html
  - apps/intake/tests/test_my_cases.py
  - apps/doctor/tests/test_detail.py
  - apps/doctor/tests/test_decision.py
  - apps/accounts/tests/test_role_labels.py
```

Depende de: slice 001 (builder + partial em `apps/cases`).

## Contexto necessário

- Builder: `apps/cases/event_trail.py::build_event_trail_context` (slice 001).
- NIR: `apps/intake/views.py::case_detail` → `_assemble_case_detail_context(case)`
  monta o contexto; template termina na thread de comunicações.
- Médico: `apps/doctor/presenters.py::build_case_detail_context` — inclui
  `decision_event` (query dedicada do `CASE_DOCTOR_DECISIONS_RECORDED` mais
  recente, ~L544); `templates/doctor/case_detail.html` termina nos cards de
  procedimentos do `_case_cards.html`.
- Asserts de ausência a reverter: `test_my_cases.py:172,584`,
  `test_detail.py:1260,1288`, `test_decision.py:756`,
  `test_role_labels.py:239,258`.
- Specs delta: `specs/intake-nir/spec.md` e `specs/doctor-decision/spec.md`
  deste change (cenários «Trilha do NIR mostra quem fez o quê» e «Trilha do
  médico mostra criação e pós-decisão"); o delta de `account-access`
  («Trilha e comunicações mostram o rótulo do papel») cobre o papel pt-BR
  nas novas trilhas.

## Goal

Detalhe do NIR e do médico exibem a trilha (card collapsible fechado por
padrão, ao final) com ator/papel pt-BR/data — quem fez o quê visível.

## Deliverables

### R1 — NIR (`intake`)

- `_assemble_case_detail_context` monta `"event_trail":
  build_event_trail_context(case)`.
- `templates/intake/case_detail.html`: `{% include "cases/_event_trail.html" %}`
  após o último card (thread de comunicações), antes de `extra_js`.
- Badge de erro do NIR em `FAILED` intocado; motivo técnico segue fora.

### R2 — Médico (`doctor`)

- `build_case_detail_context` monta `"event_trail"`; `decision_event` passa a
  ser DERIVADO da lista carregada (último `CASE_DOCTOR_DECISIONS_RECORDED`)
  em vez da query dedicada — card «Decisões registradas» inalterado.
- `templates/doctor/case_detail.html`: include do partial ao final (após o
  bloco de cards existente), antes de `extra_js`.

### R3 — Testes (RED → GREEN)

- RED NIR: detalhe do caso do NIR renderiza «Trilha de eventos» FECHADO por
  padrão (sem `show` no collapse, `aria-expanded="false"`); ao abrir, eventos
  com rótulo legível, ator + papel pt-BR, data/hora; cenário da spec — decisão
  médica com nome do médico e agendamento com nome do agendador; FAILED mantém
  badge e o motivo técnico não aparece (assert de ausência do `reason` do
  payload).
- RED médico: detalhe em `AWAITING_DOCTOR` e detalhe decidido renderizam a
  trilha fechada por padrão; criação com NIR como ator; card «Decisões
  registradas» continua com ator/data (derivação); asserts de ausência
  existentes REESCRITOS para presença.
- `test_role_labels.py`: superfícies reescritas — papel pt-BR aparece na
  trilha das novas superfícies NIR/médico (e agendador no slice 003;
  comunicações do NIR seguem cobertas), alinhado ao delta `account-access`.
- Bateria completa do AGENTS.md + suíte.

## Gates para o reviewer (2 linhas)

1. `grep -rn "Trilha de eventos" templates/intake templates/doctor` retorna o
   include/parcial; nenhum bloco de trilha duplicado inline.
2. Nenhum assert de AUSÊNCIA de «Trilha de eventos» sobra em
   `apps/{intake,doctor}/tests` e `apps/accounts/tests/test_role_labels.py`.

## Out of scope

- Agendador (slice 003); mudar `EVENT_LABELS`; filtros por papel; mover
  `decision_event` para fora do presenter.
