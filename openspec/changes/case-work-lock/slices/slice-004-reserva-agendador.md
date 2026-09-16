# Slice 004 — Reserva do caso para o agendador (espelho do médico)

## Objetivo

O mesmo desenho da reserva do médico, aplicado ao agendador: abrir o detalhe
de um caso agendável reserva o caso; POSTs de confirmação/negação exigem a
reserva vigente e a liberam ao concluir; fila do agendador mostra
🔒 Reservado / ▶ Continuar / ação livre; heartbeat e «Sair sem decidir» nas
páginas de trabalho.

## Contexto necessário (leia antes)

- `openspec/changes/case-work-lock/design.md` — seções D2, D3 e D4.
- **Diferença estrutural do HMD**: no HMD os formulários de confirmação e
  negação vivem **dentro de `scheduler:case_detail`** (`case_confirm`/
  `case_deny` são `@require_POST`, não há GET dedicado como no ATS). A
  página de trabalho do agendador É o detalhe — portanto o claim acontece no
  GET do detalhe quando o caso está agendável
  (`SCHEDULER_REQUESTED`/`AWAITING_SCHEDULING`); casos processados (aba
  processados) abrem read-only, **sem claim**.
- `apps/scheduler/views.py` — `queue` (fila por abas, `_queue_item`),
  `case_detail` (detalhe + forms), `case_confirm`/`case_deny` (POST →
  serviços `confirm_case_scheduling`/deny, flash + redirect ao detalhe).
- `apps/scheduler/urls.py` — rotas existentes.
- `templates/scheduler/case_detail.html` (forms confirmar/negar ~l.125),
  `templates/scheduler/queue.html` (cards).
- `apps/cases/locks.py` — `claim_case_lock`/`assert_case_lock`/
  `release_case_lock` + `compute_lock_display` +
  `expire_stale_locks_for_statuses` (entregues no slice 002).
- `static/js/work_lock.js` (entregue no slice 003) e
  `templates/base.html` já com `<meta name="csrf-token">`.
- `apps/scheduler/tests/test_views.py` — testes que POSTam confirm/deny
  (atualizar com claim+token, como no slice 002).
- Contexto do lock: `"scheduler_confirm"`, role `"scheduler"`; **sem**
  override de lease (segue `CASE_LOCK_LEASE_SECONDS`, 300s — tarefa curta,
  igual ao ATS).

## Requisitos verificáveis

- **R1** GET `scheduler:case_detail` de caso agendável: claim
  `context="scheduler_confirm"`, `role="scheduler"`; ambos os forms carregam
  `<input type="hidden" name="lock_token">`; trilha com `CASE_LOCK_CLAIMED`.
- **R2** GET com reserva ativa de OUTRO agendador: warning com o nome do
  portador + redirect à fila do agendador; lock inalterado. Mesmo agendador
  reabrindo: renova sem barrar.
- **R3** GET de caso processado (fora dos status agendáveis): render
  read-only **sem claim** e sem `lock_token` (comportamento atual preservado).
- **R4** POST confirm/deny sem token válido: mensagem de reserva expirada
  («Sua reserva para este caso expirou ou não é válida. Volte para a fila e
  tente novamente.») + redirect ao detalhe, **nada persiste** (rows/status/
  eventos/agendamento inalterados).
- **R5** POST válido: posse validada **sob row lock** (design D4, espelho do
  D2 item 5 do médico) — `transaction.atomic()` +
  `Case.objects.select_for_update().get(...)` + `assert_case_lock(locked,
  token)` → serviço (`confirm_case_scheduling`/negação, savepoint da mesma
  transação) → `release_case_lock` na MESMA transação → commit; flash +
  redirect. Sem janela de expiração/tomada entre validação e persistência.
- **R6** Fila do agendador (aba aguardando): expiração lazy por
  `[SCHEDULER_REQUESTED, AWAITING_SCHEDULING]` + `select_related("locked_by")`;
  cards: sem reserva → ação livre para o detalhe; próprio → «Continuar»;
  outro → «Reservado» desabilitado + nome.
- **R7** Detalhe agendável inclui `#work-lock-config` (renew/release URLs do
  scheduler, token, heartbeat 60s, grace 240s) + `#work-lock-warning` +
  carrega `work_lock.js`; rotas
  `scheduler:lock_renew`/`scheduler:lock_release` (JSON, mesma semântica do
  slice 003: posse válida → renova/libera com `success: true`; sem posse →
  `success: false`, nunca 500).
- **R8** «Sair sem decidir» no detalhe agendável: mini-form POST
  `scheduler:case_leave` (release + redirect à fila; sem token → só
  redirect).

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/scheduler/urls.py
  - apps/scheduler/views.py
  - templates/scheduler/case_detail.html
  - templates/scheduler/queue.html
  - apps/scheduler/tests/test_views.py   # R1–R6, R8 + existentes com token

allowed_incidental_files: []

out_of_scope:
  - locks.py / settings (slice 002 entregou os serviços)
  - doctor (slices 001–003)
  - serviços de agendamento (confirm_case_scheduling e negate intocados)
  - intercorrência/reabertura (fluxo existente; a reserva do caso reaberto
    segue a mesma regra genérica de status)
```

Testes existentes de `test_views.py` que POSTam confirm/deny: claim (ou
`case.lock_token` pós-GET) + `lock_token` no corpo do POST.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) | Teste/check |
| --- | --- | --- |
| R1 | `views.py`, `case_detail.html` | `test_views.py::test_open_schedulable_detail_claims_case` |
| R2 | `views.py` | `test_views.py::test_other_scheduler_blocked` + `::test_reopen_renews_own_lock` |
| R3 | `views.py` | `test_views.py::test_processed_detail_no_claim` (lock_token ausente do render) |
| R4 | `views.py` | `test_views.py::test_confirm_without_valid_token_persists_nothing` (+ variante deny) |
| R5 | `views.py` | `test_views.py::test_confirm_releases_lock` (+ teste da janela nas DUAS variantes — confirm e deny: expirar a lease ENTRE o render e o POST, ex. retroagir `locked_until`, e provar que nada persiste — espelho do slice 002; ex.: `test_deny_window_expired_token_persists_nothing`) |
| R6 | `views.py`, `queue.html` | `test_views.py::test_queue_cards_reflect_lock_state` |
| R7 | `case_detail.html`, `urls.py`, `views.py` | `test_views.py::test_work_lock_config_rendered` + `::test_lock_renew_endpoint` + `::test_lock_release_endpoint` (JSON/posse, espelho do slice 003) |
| R8 | `case_detail.html`, `urls.py`, `views.py` | `test_views.py::test_case_leave_releases_and_redirects` |

## RED

- Comando: `TEST_DB_PORT=5433 uv run pytest -q apps/scheduler/tests/test_views.py -k "claims or blocked or renews or no_claim or without_valid_token or releases_lock or reflect_lock or leave"`
- Falha esperada: sem reserva renderizada/404 nas rotas novas — motivo de
  rota/render, não de setup (status 200/302 alcançados).

## GREEN / verificação local

- `TEST_DB_PORT=5433 uv run pytest -q apps/scheduler/tests/test_views.py apps/scheduler/tests/test_incident.py` — exit 0
- `uv run ruff check apps && uv run mypy apps` — exit 0

## Critérios de aceitação

- [ ] R1–R8 demonstrados (RED→GREEN)
- [ ] Dois agendadores (unidades 1 e 2) não se bloqueiam além do caso
      reservado (fila completa permanece visível para ambos)
- [ ] Detalhe processado sem claim (R3) — sem regressão da aba processados
- [ ] Sem arquivo fora do blast radius; sem commits/tasks.md
