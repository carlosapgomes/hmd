# Slice 005: Comunicações operacionais por caso

## Objetivo

Thread de comunicações append-only por caso: mensagens `user` (manual, com autor + papel ativo no momento do post) e `system` (automáticas, projetadas de eventos relevantes da FSM via signal, sem autor e sem notificação).

## Contexto necessário (contexto zero)

- Slices 001–004 entregues: catálogo, Case+FSM+CaseEvent, CaseProcedure+serviços, locks.
- Referência (somente-leitura): `/projects/dev/ats-web/apps/cases/models.py::CaseCommunicationMessage` (message_type user|system, author PROTECT null, author_role, body, source_event O2O, system_event_type) e `/projects/dev/ats-web/apps/cases/services.py::create_system_communication_notice_for_event` + signal `CaseEvent.post_save` em `apps/cases/signals.py`.
- Design: `../design.md` D8 (shape, conjunto inicial de eventos projetados, system sem notificação).
- Spec: `../specs/case-management/spec.md` — Requirement "Comunicações operacionais por caso" (2 cenários).

## Requisitos

- **R1** `CaseCommunicationMessage`: `message_id UUID pk`, `case FK CASCADE`, `message_type ∈ {user, system}` default user, `author FK PROTECT null`, `author_role` (blank), `body`, `source_event O2O → CaseEvent (null)`, `system_event_type` (blank), `created_at`; ordering por `created_at`; migration.
- **R2** Serviço `post_user_communication(case, *, user, body)`: cria mensagem user com `author_role` = papel ativo da sessão do usuário (reusar o mecanismo do `apps/accounts` — papel ativo vem da sessão do request; o serviço recebe o papel explícito ou lê do request no caller) — neste slice: `post_user_communication(case, *, user, role, body)` com papel explícito (views dos changes 04+ extraem da sessão).
- **R3** Signal `CaseEvent.post_save` → `create_system_communication_notice_for_event(event)`: para `event_type` no conjunto inicial (`CASE_STATUS_AWAITING_DOCTOR`, `CASE_STATUS_DOCTOR_DENIED`, `CASE_STATUS_SCHEDULING_CONFIRMED`, `CASE_STATUS_SCHEDULING_DENIED` — constantes em código), cria mensagem `system` sem autor, com `source_event` O2O e texto canônico por tipo (ex.: "Caso disponível para decisão médica"); tipos fora do conjunto → nenhuma mensagem.
- **R4** Idempotência da projeção: um evento gera no máximo uma mensagem system (`source_event` O2O garante; signal disparado 2× não duplica).
- **R5** Mensagens system não geram notificação/badge/estado de leitura (nada além da row; notificações são change 11) — verificado por inspeção (nenhum modelo de notificação é criado).
- **R6** Testes: post user grava autor+papel+timestamp e aparece na thread ordenada; transição projetada gera mensagem system vinculada ao evento sem autor; evento fora do conjunto não projeta; re-save do mesmo evento não duplica (O2O).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/cases/models.py`, `migrations/000X_communications.py` | `test_communications.py::test_message_fields` (implícito) |
| R2 | `apps/cases/communications.py` (ou services.py) | `::test_post_user_message_records_role` |
| R3 | `apps/cases/signals.py`, `apps/cases/apps.py` (ready) | `::test_fsm_event_projects_system_message`, `::test_non_projected_event_no_message` |
| R4 | `apps/cases/signals.py` | `::test_projection_idempotent` |
| R5 | — | `rg -n "Notification" apps/` → vazio (check de ausência) |
| R6 | `apps/cases/tests/test_communications.py` | `uv run pytest apps/cases/tests/test_communications.py` |

## RED

- Comando: `uv run pytest apps/cases/tests/test_communications.py`
- Falha esperada: `ImportError`/`FieldError` — modelo/serviço/signal inexistentes; transição não gera mensagem.

## GREEN / verificação local

- `uv run pytest apps/cases/tests/test_communications.py` — exit 0
- `uv run pytest apps/cases/tests/` — exit 0 (regressão do app completo)
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/models.py            # + CaseCommunicationMessage
  - apps/cases/communications.py    # serviço user + projeção system (ou services.py)
  - apps/cases/signals.py
  - apps/cases/apps.py              # ready() conecta signal
  - apps/cases/migrations/000X_communications.py
  - apps/cases/tests/test_communications.py
allowed_incidental_files: []
out_of_scope:
  - notificações/badges/estado de leitura (change 11)
  - UI da thread (changes 04+)
  - mensagens de usuário entre papéis arbitrários (fica o serviço; telas depois)
  - novos event_types projetados além do conjunto inicial (cada change de fluxo estende)
```

Escale ao parent se: a projeção exigir registrar signals fora do app `cases`; precisar de template de texto por evento além de constantes.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz (2 cenários da spec cobertos)
- [ ] Projeção idempotente (O2O comprovado)
- [ ] Nenhuma notificação criada (R5)
- [ ] Gate parcial do slice verde
