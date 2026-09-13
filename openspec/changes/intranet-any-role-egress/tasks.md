# Tasks — intranet-any-role-egress

## Preflight

- [ ] Baseline: confirmar árvore limpa em `main`, registrar `BASE_REF` e rodar a suíte completa uma vez (`TEST_DB_PORT=55435 uv run pytest -q`)

## Implementação

- [ ] Slice 001 — guard decide pelo conjunto de papéis (middleware + testes + ADR-0002 revisado) — `slices/slice-001-guard-by-role-set.md`

## Gate final

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy . && TEST_DB_PORT=55435 uv run pytest` — tudo verde, sem regressão vs. baseline
- [ ] `openspec validate intranet-any-role-egress --strict`

## Pós-gate (parent)

- [ ] Atualizar `PROJECT_CONTEXT.md` com o estado do change
