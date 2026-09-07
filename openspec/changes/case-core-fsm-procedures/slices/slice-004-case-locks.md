# Slice 004: Locks/lease de concorrência por caso

## Objetivo

Exclusividade de mutação por caso com lease temporária: claim (com token, contexto e papel), assert de posse, release, renovação e expiração de leases vencidas — com eventos na trilha e rejeição explícita de claims conflitantes.

## Contexto necessário (contexto zero)

- Slices 001–003 entregues: catálogo, Case+FSM+CaseEvent, CaseProcedure+serviços.
- Referência (somente-leitura): `/projects/dev/ats-web/apps/cases/models.py` (campos `locked_by/locked_at/locked_until/lock_token/lock_context/lock_role`) e `/projects/dev/ats-web/apps/cases/services.py` (`claim_case_lock`, `assert_case_lock`, `release_case_lock`, `renew_case_lock`, `expire_stale_locks_for_statuses`); settings `CASE_LOCK_LEASE_SECONDS` no `config/settings/base.py` do ats-web.
- Design: `../design.md` D6 (campos + serviços + `select_for_update` + eventos `CASE_LOCK_*`; lease por env `CASE_LOCK_LEASE_SECONDS` **default 300s**). **Divergências vs ats-web**: serviços em `apps/cases/locks.py` (no ats-web vivem em `services.py`); `renew` com evento e `expire_stale_locks()` genérica são extensões HMD (ats-web tem `expire_stale_locks_for_statuses` sem eventos); sem variantes de lease por papel.
- Spec: `../specs/case-management/spec.md` — Requirement "Locks de concorrência por caso" (3 cenários).

## Requisitos

- **R1** Campos de lock no `Case` (migration): `locked_by FK SET_NULL null`, `locked_at`, `locked_until` (indexado), `lock_token UUID null`, `lock_context` (max 40, blank), `lock_role` (max 30, blank).
- **R2** `claim_case_lock(case, *, user, context, role=None, lease_seconds=None)`: dentro de `transaction.atomic()` + `select_for_update` no case — livre **ou** lease expirada (grava evento `CASE_LOCK_EXPIRED` e assume) → novo `lock_token`, `locked_until = now + lease` (default settings, 300s), evento `CASE_LOCK_CLAIMED`; lock ativo de outro ator → `CaseLockConflict` (erro explícito com dono/contexto) sem alterar nada.
- **R3** `assert_case_lock(case, token)`: token divergente ou sem lock → `CaseLockConflict`; válido → ok (usado pelos serviços de mutação do slice 003 e futuros).
- **R4** `release_case_lock(case, token)`: só o portador libera (token errado → conflito); evento `CASE_LOCK_RELEASED`.
- **R5** `renew_case_lock(case, token, lease_seconds=None)`: portador estende `locked_until`; evento `CASE_LOCK_RENEWED`.
- **R6** `expire_stale_locks()`: varre locks com `locked_until < now`, limpa e grava `CASE_LOCK_EXPIRED` (executável por chamada agendada futura; neste change, função + teste).
- **R7** Testes: claim concede token/lease; claim conflitante negado com lock original intacto; lease expirada é assumida com evento; assert/release/renew com token errado conflitam; expiração varre; concorrência real (duas claims em transações sobrepostas → exatamente uma vence) — usar `transaction.atomic` + threads ou simular com select_for_update em teste de integração.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/cases/models.py`, `migrations/000X_case_locks.py` | `test_locks.py::test_lock_fields_migration` (implícito no setup) |
| R2 | `apps/cases/locks.py` | `::test_claim_grants_token`, `::test_conflicting_claim_rejected`, `::test_expired_lease_taken_over` |
| R3 | `apps/cases/locks.py` | `::test_assert_requires_valid_token` |
| R4/R5 | `apps/cases/locks.py` | `::test_release_and_renew_by_token_holder_only` |
| R6 | `apps/cases/locks.py` | `::test_expire_stale_sweep` |
| R7 | `apps/cases/tests/test_locks.py` | `uv run pytest apps/cases/tests/test_locks.py` + `rg -n "CASE_LOCK_LEASE_SECONDS" config/settings/base.py .env.example` |

## RED

- Comando: `uv run pytest apps/cases/tests/test_locks.py`
- Falha esperada: `ModuleNotFoundError: apps.cases.locks` — mecanismo inexistente.

## GREEN / verificação local

- `uv run pytest apps/cases/tests/test_locks.py` — exit 0
- `uv run pytest apps/cases/tests/` — exit 0 (regressão do app)
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/locks.py
  - apps/cases/models.py            # + campos de lock
  - apps/cases/migrations/000X_case_locks.py
  - apps/cases/tests/test_locks.py
  - config/settings/base.py         # CASE_LOCK_LEASE_SECONDS
  - .env.example
out_of_scope:
  - integração automática lock→transições/serviços (os consumers ligam nos changes 07/08/workers)
  - celery/q2 schedule da expiração (workers são changes 04+)
  - locks por status específico (variantes do ats-web) — HMD usa lease única default
```

Escale ao parent se: o teste de concorrência real exigir infraestrutura além de threads/select_for_update; precisar de lock granular por status.

## Critérios de aceitação

- [ ] R1–R7 comprovados pelos comandos da matriz (3 cenários da spec cobertos)
- [ ] Claim conflitante nunca sobrescreve lock válido
- [ ] Todos os eventos `CASE_LOCK_*` na trilha
- [ ] Gate parcial do slice verde
