# Slice 001 — Filas de espera ordenadas por tempo de tela (médico + agendador)

```yaml
expected_files:
  - apps/doctor/views.py
  - templates/doctor/queue.html
  - apps/scheduler/views.py
  - templates/scheduler/queue.html
  - apps/doctor/tests/test_queue.py
  - apps/scheduler/tests/test_views.py
```

## Contexto necessário

- `apps/doctor/views.py` `_queue_cases` (~141-156): `order_by("created_at",
  "case_id")` — única fonte da fila para TODAS as abas; items montados
  adiante (nome/nº/recebido/procedimentos) e renderizados em
  `templates/doctor/queue.html` (~46-70: `item.patient_name`,
  `item.agency_record_number`, `item.created_at`).
- `apps/scheduler/views.py`: row dict (~150-170: `patient_name`,
  `agency_record_number`, `created_at`, …) + `queue` (~174-187,
  `order_by("created_at", "case_id")`); template `scheduler/queue.html`
  (~36 exibe nome).
- Campos `patient_age`/`days_on_screen` existem no `Case` (change
  `sesab-header-extraction`).
- Testes existentes das filas: `apps/doctor/tests/test_queue.py`
  (`apps/scheduler/tests/test_views.py`) — os asserts de ordem FIFO
  atuais serão ATUALIZADOS para o novo contrato (sort por tempo de tela).

## Goal

Filas de médico e agendador ordenadas por tempo de tela oficial
(`days_on_screen` desc nulls-last, desempate FIFO) com cards exibindo
nome+idade, tempo de espera (timesince) e badge de dias em tela.

## Deliverables

### R1 — Ordenação

- As DUAS filas: `order_by(F("days_on_screen").desc(nulls_last=True),
  "created_at", "case_id")` (import `from django.db.models import F`).
  Nenhuma outra cláusula/filter/prefetch muda.

### R2 — Cards

- Doctor (`templates/doctor/queue.html`): idade junto do nome —
  `{{ item.patient_name }}{% if item.patient_age %} · {{ item.patient_age }} a{% endif %}`;
  linha de tempo `⏱ Aguardando há {{ item.created_at|timesince }}` +
  `{% if item.days_on_screen %} · {{ item.days_on_screen }} d em tela{% endif %}`
  (badge pequeno ou texto muted — siga o estilo visual dos cards atuais).
- Scheduler: row dict ganha `patient_age`/`days_on_screen`; template com a
  MESMA composição.

### R3 — Testes (RED→GREEN)

- Novos/ajustados (RED): ordenação com `days_on_screen` 10/3/None (None ao
  fim, FIFO no desempate) nas DUAS filas — asserts por ordem dos `case_id`
  na resposta; card renderiza `84 a` + `6 d em tela` + `Aguardando há`;
  idade ausente → sem `· X a`; `days_on_screen` ausente → sem badge;
  **atualizar** os asserts FIFO existentes para o novo contrato (casos sem
  `days_on_screen` continuam FIFO entre si).
- Bateria completa do AGENTS.md: pytest (alvo + suíte) + ruff check/format
  + mypy + `manage.py check --settings=config.settings.dev`.

## Gates para o reviewer (2 linhas)

1. A ordenação nova é a ÚNICA mudança de queryset nas duas views (diff
   mostra `order_by` trocado; filtros/prefetch/distinct intactos) e cobre
   TODAS as abas (mesma função/queryset).
2. Teste de ordenação é DISCRIMINANTE: casos com `days_on_screen`
   distintos E um `None` mais antigo — falharia com o FIFO antigo
   (proved: RED).

## Out of scope

- Meus casos do NIR (slice 002); painel (zero-PHI intacto); detalhes;
  atualização dinâmica de `days_on_screen`.
