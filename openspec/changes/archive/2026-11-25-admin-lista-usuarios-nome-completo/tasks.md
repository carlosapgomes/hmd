# Tasks

- [x] S1 — Coluna «Nome completo» e busca por nome na lista de usuários do Django admin
  Contract: slices/slice-001-coluna-nome-completo-lista-usuarios.md

## Gate do change

- [x] G — Quality gate completo (`AGENTS.md` §2) + `openspec validate --strict`,
  executado após S1 verde.

> Nota de execução (2026-11-25): implementado e verificado na mesma sessão, a
> pedido do dono — sem revisão independente por slice. TDD com RED genuíno
> (9 failed antes do admin → 9 passed). Gate: ruff/format/mypy limpos, pytest
> 2870 passed — contando com a correção do teste flaky de `apps/doctor`
> (change `test-espera-herdada-data-local`). Relatório:
> `/tmp/hmd-slice-admin-nome-completo-001-report.md`.
