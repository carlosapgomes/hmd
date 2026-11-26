# Tasks

Mudança somente de teste (`skip_specs: true`); quality gate do `AGENTS.md` §2;
relatório em `/tmp/`, fora do Git.

- [x] S1 — Pré-condição do teste de espera herdada compara data local
  Contract: slices/slice-001-pre-condicao-data-local.md

## Gate do change

- [x] G — Quality gate completo (`AGENTS.md` §2) + `openspec validate --strict`,
  executado após S1 verde.

> Nota de execução (2026-11-25): implementado e verificado na mesma sessão, a
> pedido do dono — sem revisão independente por slice. RED no relógio real
> (22:14 -03, dentro da janela 21:00–00:00) → GREEN no relógio real e com
> `now` fixado antes (20:30) e depois (22:00) da fronteira UTC. Gate completo
> às 22:15–22:18 (-03): ruff/format/mypy limpos, pytest 2870 passed. Relatório:
> `/tmp/hmd-slice-test-espera-herdada-001-report.md`.
