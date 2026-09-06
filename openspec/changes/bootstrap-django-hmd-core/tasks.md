# Tasks: bootstrap-django-hmd-core

> Execução slice a slice (1 por vez, TDD RED→GREEN→REFACTOR, worker implementa + reviewer valida + parent commita).
> Cada slice tem arquivo próprio em `slices/` com contexto zero, requisitos, blast radius e plano de testes.

## 1. Fundação e toolchain

- [x] 1.1 Slice 001 — Scaffold Django + toolchain + documentação viva (ADRs, AGENTS.md, PROJECT_CONTEXT.md, README). Verificação: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest` exit 0 com smoke tests; ver `slices/slice-001-foundation-tooling.md`

## 2. Banco e ambientes compose

- [x] 2.1 Slice 002 — Settings de banco (DATABASE_URL/DB_*), compose dev/test com PostgreSQL 17 + extensões. Verificação: `docker compose -f docker-compose.test.yml up -d` + `uv run pytest` verde contra banco de teste; `manage.py migrate` aplica no compose dev; ver `slices/slice-002-database-compose.md`

## 3. Contas: modelo e seed

- [x] 3.1 Slice 003 — `User` + `Role` (5 papéis) + `account_status` + conselho profissional + `seed_admin`. Verificação: testes de modelo/seed verdes (`apps/accounts/tests/`); seed idempotente comprovado; ver `slices/slice-003-accounts-models-seed.md`

## 4. Autenticação local e templates base

- [x] 4.1 Slice 004 — Login/logout com ModelBackend + backend com checagem de `account_status`, base.html hospitalar, home placeholder, perfil. Verificação: testes de fluxo de login (client Django) verdes; ver `slices/slice-004-auth-views-templates.md`

## 5. Multi-role e papel ativo

- [x] 5.1 Slice 005 — `ActiveRoleMiddleware`, switch-role, context processor, `@role_required`. Verificação: cenários das specs (auto-set, multi-role redirect, troca, 403) verdes; ver `slices/slice-005-active-role-switch.md`

## 6. Intranet guard (nir)

- [x] 6.1 Slice 006 — `IntranetGuardMiddleware` (CIDR + trusted proxy + bypass multi-role + paths isentos). Verificação: cenários de bloqueio/liberação da spec verdes; ver `slices/slice-006-intranet-guard.md`

## 7. Gate final do change

- [x] 7.1 Executar quality gate completo (`uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`) e registrar resultado no relatório do change — **resultado (2026-09-07): ruff check ✅ · ruff format ✅ (50 arquivos) · mypy ✅ (35 arquivos) · pytest ✅ 61 passed (banco de teste PostgreSQL 17 via compose) · manage.py check ✅ · openspec validate ✅**
- [x] 7.2 Atualizar `PROJECT_CONTEXT.md` com o estado pós-change e preparar arquivamento (`openspec archive bootstrap-django-hmd-core`) — PROJECT_CONTEXT atualizado; arquivamento pendente de revisão final do dono
