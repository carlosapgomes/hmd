# Slice 002 — Reserva do caso para o médico (claim, fila e POST)

## Objetivo

Ao abrir a página de trabalho, o caso fica **reservado** para o médico:
claim atômico com lease longa (`doctor_decision`), fila mostrando
🔒 Reservado / ▶ Continuar avaliação / Avaliar, POST da decisão exigindo a
reserva vigente (`lock_token`) e liberação determinística ao decidir.
Reserva vencida expira de forma lazy na fila. Sem JavaScript neste slice
(heartbeat vem no 003).

## Contexto necessário (leia antes)

- `openspec/changes/case-work-lock/design.md` — seções D2 e D5.
- `apps/cases/locks.py` — `claim_case_lock`, `assert_case_lock`,
  `release_case_lock`, `CaseLockConflictError`,
  `_default_lease_seconds` (hoje lê só `CASE_LOCK_LEASE_SECONDS`);
  eventos em `apps/cases/events.py`.
- `apps/doctor/views.py` — `case_decide` (GET/POST; guards de acesso e de
  status já existem; POST chama `record_doctor_procedure_decisions` e
  redireciona ao detalhe com flash).
- `apps/doctor/views.py` — view da fila médica (monta cards da aba
  aguardando/decididos; hoje sem `locked_by`).
- `templates/doctor/queue.html` — cards da aba aguardando (ação única atual).
- `apps/cases/tests/test_locks.py` — testes existentes de locks (padrões de
  assert/eventos).
- `apps/doctor/tests/test_decision.py` — ÚNICO módulo que submete a view
  `case_decide` (atualizar para claim+token).
- Referência de semântica (produção): `/projects/dev/ats-web/apps/cases/
  services.py::_CONTEXT_LEASE_SETTINGS`, `compute_lock_display`,
  `expire_stale_locks_for_statuses`; `apps/doctor/views.py` (decision/submit).

## Requisitos verificáveis

- **R1** Lease por contexto em `locks.py`: `_CONTEXT_LEASE_SETTINGS` mapeia
  `"doctor_decision"` → setting `CASE_LOCK_LEASE_SECONDS_DOCTOR` (default
  3600); precedência override > contexto > default (300).
- **R2** `compute_lock_display(case, user)` retorna
  `is_locked`, `is_locked_by_current_user`, `locked_by_display`,
  `locked_until` (iso), `lock_context`; lease vencida = não travado.
- **R3** `expire_stale_locks_for_statuses(statuses)` limpa locks vencidos
  apenas dos casos nos status dados (sem eventos; genérica existente
  permanece intacta).
- **R4** GET `case_decide` (caso em `AWAITING_DOCTOR`): claim com
  `context="doctor_decision"`, `role="doctor"`; formulário carrega
  `<input type="hidden" name="lock_token">`; trilha ganha
  `CASE_LOCK_CLAIMED`.
- **R5** GET com reserva ativa de OUTRO: `messages.warning` com o nome do
  portador + redirect à fila; nada muda no lock. Mesmo médico reabrindo:
  renova (novo token) sem barrar.
- **R6** POST sem token/inválido/expirado: re-render do formulário com
  `lock_error` visível («Sua reserva para este caso expirou ou não é válida.
  Volte para a fila e tente novamente.») e **nada persiste** (rows/status/
  eventos de decisão inalterados).
- **R7** POST válido: a view valida a posse **sob row lock** —
  `transaction.atomic()` + `Case.objects.select_for_update().get(...)` +
  `assert_case_lock(locked, token)` → `record_doctor_procedure_decisions`
  (savepoint da mesma transação) → `release_case_lock` na MESMA transação
  → commit — sem janela de expiração/tomada entre validação e persistência
  (design D2 item 5). Resultado: decisão persiste, caso sem lock e trilha
  com `CASE_LOCK_RELEASED`.
- **R8** Fila médica: expiração lazy (`[AWAITING_DOCTOR]`) +
  `select_related("locked_by")`; card aguardando: sem reserva → «Avaliar»;
  próprio → «Continuar avaliação»; outro → «Reservado» desabilitado + nome.

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/locks.py
  - config/settings/base.py            # CASE_LOCK_LEASE_SECONDS_DOCTOR = 3600
  - apps/doctor/views.py               # case_decide (GET/POST) + fila
  - templates/doctor/queue.html        # cards com estado de reserva
  - templates/doctor/decide.html       # hidden lock_token + bloco lock_error
  - apps/cases/tests/test_locks.py     # R1–R3
  - apps/doctor/tests/test_decision.py # R4–R7 (novos + existentes com token)
  - apps/doctor/tests/test_queue.py    # R8

allowed_incidental_files: []

out_of_scope:
  - endpoints renew/release e JS (slice 003)
  - «Sair sem decidir» com release (slice 003; aqui segue link simples)
  - scheduler (slice 004)
  - services de decisão (`record_doctor_procedure_decisions` intocado)
```

Atualização dos testes existentes de `test_decision.py` que POSTam a decisão:
claim na sessão do teste (ou leitura do `case.lock_token` pós-GET) e campo
`lock_token` no POST — sem isso eles falham por R6 (comportamento novo
correto).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) | Teste |
| --- | --- | --- |
| R1 | `locks.py`, `settings/base.py` | `test_locks.py::test_lease_por_contexto_doctor` |
| R2 | `locks.py` | `test_locks.py::test_compute_lock_display_estados` |
| R3 | `locks.py` | `test_locks.py::test_expire_stale_locks_por_status` |
| R4 | `views.py`, `decide.html` | `test_decision.py::test_open_work_page_claims_case` |
| R5 | `views.py` | `test_decision.py::test_other_doctor_blocked_by_active_lock` + `::test_reopen_renews_own_lock` |
| R6 | `views.py`, `decide.html` | `test_decision.py::test_submit_without_valid_token_persists_nothing` |
| R7 | `views.py` | `test_decision.py::test_decision_releases_lock` (+ teste da janela: expirar a lease ENTRE o render e o POST — ex. retroagir `locked_until` — e provar que nada persiste; provas de atomicidade via rows/eventos/status) |
| R8 | `views.py`, `queue.html` | `test_queue.py::test_awaiting_cards_reflect_lock_state` |

## RED

- Comando: `TEST_DB_PORT=5433 uv run pytest -q apps/cases/tests/test_locks.py -k "contexto or display or por_status"`
- Falha esperada: `AttributeError`/`ImportError` (funções/setting não existem).
- Comando: `TEST_DB_PORT=5433 uv run pytest -q apps/doctor/tests/test_decision.py -k "claims or blocked or renews or without_valid_token or releases_lock"`
- Falha esperada: asserts de reserva ausente no caso/`lock_error` não
  renderizado — e NÃO por 403/404 de setup.

## GREEN / verificação local

- `TEST_DB_PORT=5433 uv run pytest -q apps/cases/tests/test_locks.py` — exit 0
- `TEST_DB_PORT=5433 uv run pytest -q apps/doctor/tests/test_decision.py apps/doctor/tests/test_queue.py apps/doctor/tests/test_detail.py` — exit 0
- `uv run ruff check apps && uv run mypy apps` — exit 0

## Critérios de aceitação

- [ ] R1–R8 demonstrados (RED→GREEN)
- [ ] Trilha: claim/release gravados com contexto `doctor_decision`
- [ ] POST sem reserva não grava NADA (verificado por rows/eventos/status)
- [ ] Sem arquivo fora do blast radius; sem commits/tasks.md
