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

- Doctor (`templates/doctor/queue.html`): idade junto do nome com
  `is not None` — `{{ item.patient_name }}{% if item.patient_age is not None %} · {{ item.patient_age }} a{% endif %}`
  (`0 a` EXIBE); rótulo de tempo por aba: «⏱ Aguardando há …» na aba
  aguardando, «Recebido há …» nas abas decididos (aba corrente disponível
  no contexto); `· {{ item.days_on_screen }} d em tela` com `is not None`
  (`0 d em tela` EXIBE).
- Scheduler: row dict ganha `patient_age`/`days_on_screen`; template com a
  MESMA composição (mesmos `is not None` e rótulo por aba).

### R3 — Testes (RED→GREEN)

- Novos/ajustados (RED): ordenação DISCRIMINANTE nas DUAS filas com GIVEN
  explícito `days_on_screen` 10, 3 e None com `created_at` CONFLITANTES
  (o None é o mais antigo; o 10 é o mais recente) — asserts pela ORDEM dos
  `case_id` na resposta (falha contra o FIFO antigo: proved no RED);
  card renderiza `84 a` + `6 d em tela` + `Aguardando há` (aba ativa);
  `Recebido há` na aba histórica (assert por aba); **zero válido**:
  `patient_age=0` → `0 a` renderizado e `days_on_screen=0` → `0 d em tela`
  renderizado; idade/dias ausentes → sufixo/badge ausentes;
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
