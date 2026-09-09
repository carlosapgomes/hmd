# Slice 001: UserNotification — model + services + signal de marcos

## Objetivo

Notificações existem como dado: `UserNotification` em apps/accounts
(migration), serviço `create_milestone_notifications(event)` idempotente e
signal `CaseEvent.post_save` criando notificações para os 3 marcos
(criador ×2 + fan-out schedulers), sem PHI e sem nunca bloquear o caso.

## Contexto necessário

- Design D1 (`openspec/changes/dashboard-notifications-pwa/design.md`) —
  campos exatos, unique constraint, conjunto de marcos.
- `apps/cases/events.py` — `CaseEventType.CASE_STATUS_FINAL_REPLY_POSTED`
  (payload `source`: `DOCTOR_DENIED|SCHEDULING_CONFIRMED|SCHEDULING_DENIED`),
  `CASE_STATUS_SCHEDULER_REQUESTED`, `CASE_STATUS_AWAITING_SCHEDULING` (a
  reabertura do 08 carrega `payload["reason"]` — ver
  `Case.reopen_scheduling` em `apps/cases/models.py`: `extra_payload=
  {"reason": reason}`).
- `apps/accounts/models.py` — `Role` (M2M `User.roles`, `name` único;
  fan-out: `User.objects.filter(roles__name="scheduler")`, distinct);
  migration mais recente de accounts (nova migration depois dela).
- Signal pattern: `apps/attachments/signals.py` (receiver `post_save,
  sender=CaseEvent`, filtro `created`, registro em `apps.py ready()`;
  `transaction.on_commit` NÃO é necessário aqui — a row de notificação na
  mesma transação do evento é aceitável e mais simples; se o rollback do
  caso ocorrer, notificação some junto — desejável).
- `Case.created_by` é o NIR criador (notificável). Event payload/`actor`:
  usar `event.case` para destinatário; NÃO notificar o próprio ator do
  evento se ele for o criador? **Decisão**: notifica mesmo assim (o NIR
  quer saber da resposta final mesmo quando ator — não há caso real em que
  o criador publica a resposta final: quem publica é doctor/scheduler).

## Requisitos verificáveis

- **R1** Model exato (D1): UUID pk, FKs CASCADE (`recipient` related
  `notifications`, `case`, `event` nullable), `notification_type` choices
  dos 3 marcos, `title` ≤160, `body_preview` ≤240, `created_at`
  auto_now_add, `read_at` null; `UniqueConstraint(recipient, event)`;
  índices (`recipient`,`read_at`,`created_at`) e (`case`,`created_at`);
  ordering `-created_at`; migration sem drift.
- **R2** `apps/accounts/notifications.py::create_milestone_notifications(
  event) -> list[UserNotification]`: fora do conjunto de marcos → `[]`
  sem tocar nada; `FINAL_REPLY_POSTED` → criador, título "Resposta final
  disponível", preview por `payload["source"]` (mapa fixo PT-BR: negativa
  médica / negativa de agendamento / agendamento confirmado);
  `SCHEDULER_REQUESTED` → fan-out schedulers, título "Caso pronto para
  agendamento"; `AWAITING_SCHEDULING` com `"reason" in payload` → criador,
  título "Caso reaberto por intercorrência" (SEM o motivo no preview —
  motivo é interno do fluxo; preview fixo "Reconfirme os dados do caso").
  Sem PHI: título/preview de tabelas fixas.
- **R3** Idempotência: `get_or_create` por (`recipient`, `event`) —
  chamada dupla devolve as mesmas rows (teste com 2 invocações).
- **R4** Signal `apps/accounts/signals.py` (wired no `ready()` de
  `AccountsConfig` — cuidado com o import do receiver, padrão
  apps/attachments/apps.py): `post_save` sender `CaseEvent`, `created`,
  chama o serviço envolto em `try/except Exception` com `logger.exception`
  — notificação é suplementar, transição do caso NUNCA falha por causa
  dela (teste: serviço que explode → evento gravado normalmente).
- **R5** Testes: cada marco cria destinatário certo (criador; todos os
  schedulers com 2 schedulers no fixture + 1 nir sem papel scheduler);
  AWAITING_SCHEDULING sem `reason` (entrada normal na fila pós-decisão
  parcial) NÃO notifica o criador; evento fora do conjunto não notifica;
  idempotência; falha do serviço não bloqueia; preview por source ×3.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/models.py`, `apps/accounts/migrations/000X_usernotification.py` | `test_notification_fields`, `makemigrations --check` |
| R2 | `apps/accounts/notifications.py` | `test_final_reply_notifies_creator_*` ×3 sources, `test_scheduler_requested_fanout`, `test_reopen_notifies_creator`, `test_plain_awaiting_scheduling_no_notification` |
| R3 | `apps/accounts/notifications.py` | `test_idempotent_double_call` |
| R4 | `apps/accounts/signals.py`, `apps/accounts/apps.py` | `test_signal_creates_on_event`, `test_signal_failure_does_not_block_event` |
| R5 | `apps/accounts/tests/test_notifications*.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/models.py                    # +UserNotification
  - apps/accounts/migrations/000X_usernotification.py
  - apps/accounts/notifications.py
  - apps/accounts/signals.py
  - apps/accounts/apps.py                      # ready() wiring
  - apps/accounts/tests/test_notifications_model.py
  - apps/accounts/tests/test_notifications_service.py

out_of_scope:
  - UI/badge/lista (slice 002); dashboard (003); PWA (004); manual (005)
  - apps/cases (nenhuma mudança); views/forms existentes
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_notifications_service.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.accounts.notifications'`.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/` — exit 0
  (regressão de accounts).
- `uv run ruff check apps/accounts && uv run ruff format --check apps/accounts`
- `uv run mypy .`
- `uv run python manage.py makemigrations --check --dry-run`
- NOTA: DB de teste reusada — se surgir erro de coluna/migration, rode
  `--create-db` UMA vez.

## Critérios de aceitação

- [ ] R1–R5 comprovados; marcos certos notificam destinatários certos
- [ ] Entrada normal em AWAITING_SCHEDULING (sem reason) NÃO notifica
- [ ] Idempotência estrutural (unique + get_or_create) testada
- [ ] Falha de notificação jamais bloqueia transição do caso
- [ ] Gate parcial do slice verde
