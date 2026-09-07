# Tasks: ad-kerberos-authentication

> Execução slice a slice (worker + reviewer + parent commita, `/slice-loop`). Cada slice tem arquivo próprio em `slices/`.
> **Pré-condição**: change `bootstrap-django-hmd-core` arquivado (specs de `account-access` promovidas para `openspec/specs/`), pois este change usa MODIFIED/RENAMED sobre elas.

## 0. Preflight

- [x] 0.1 Confirmar working tree limpa, registrar `BASE_REF`; suíte verde uma vez (baseline do change anterior serve) — executado no início da execução: árvore limpa, BASE_REF `5c48785`, baseline do change 01 (61 testes) válida

## 1. Provisionamento

- [x] 1.1 Slice 001 — `User.ad_upn` (único/opcional) + migration + Django admin de User/Role (provisionamento administrativo). Ver `slices/slice-001-ad-upn-provisioning.md`

## 2. Cliente Kerberos

- [x] 2.1 Slice 002 — `kerberos.py`: `KerberosAuthResult` + wrapper minikerberos injetável + failover restrito a transporte + extração de código por protocolo. Ver `slices/slice-002-kerberos-client.md`

## 3. Backend e login

- [x] 3.1 Slice 003 — `KerberosBackend` + `LocalAccountBackend` recusando usuários AD + ordem de backends + mensagens "serviço indisponível" vs "credenciais inválidas" + ADR-0004. Ver `slices/slice-003-kerberos-backend-login.md`

## 4. Anti-lockout

- [x] 4.1 Slice 004 — Rate-limit por CPF/IP+CPF via cache, recusa pré-KDC, sucesso zera, env-configurável. Ver `slices/slice-004-lockout-rate-limit.md`
- [x] 4.2 **Emenda pós-review (aprovada pelo dono)**: falhas por indisponibilidade do serviço (`request.kerberos_unavailable`) não contam para o limite local — spec emendada (cenário "Falha por indisponibilidade não conta para o limite"), slice 004 R2/R5/matrIZ atualizados, fix + testes via ciclo worker/reviewer

## 5. UX zero papéis

- [x] 5.1 Slice 005 — `switch_role` com zero papéis encerra sessão com mensagem (fecha nota de arquivamento do change 01). Ver `slices/slice-005-switch-role-zero-roles.md`

## 6. Gate final do change

- [x] 6.1 Quality gate completo (`uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`) + `openspec validate ad-kerberos-authentication`; registrar resultado no relatório do change — **resultado (2026-09-06): ruff check ✅ · ruff format ✅ (73 arquivos) · mypy ✅ (47 arquivos) · pytest ✅ 145 passed · manage.py check ✅ · openspec validate ✅**
- [x] 6.2 Atualizar `PROJECT_CONTEXT.md` (estado pós-change, env vars novas, `ad_check` operacional) e preparar arquivamento — PROJECT_CONTEXT atualizado; arquivamento pendente de revisão final do dono
