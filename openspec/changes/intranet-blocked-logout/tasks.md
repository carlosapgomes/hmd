# Tasks — intranet-blocked-logout

## Preflight

- [x] Baseline: confirmar árvore limpa em `main`, registrar `BASE_REF` e rodar a suíte completa uma vez (`TEST_DB_PORT=55435 uv run pytest -q`)

## Implementação

- [x] Slice 001 — logout no bloqueio + página de bloqueio com retorno ao login — `slices/slice-001-blocked-logout.md`

## Gate final

- [x] `uv run ruff check . && uv run ruff format --check . && uv run mypy . && TEST_DB_PORT=55435 uv run pytest` — tudo verde, sem regressão vs. baseline
- [x] `openspec validate intranet-blocked-logout --strict`

## Pós-gate (parent)

- [x] Atualizar `PROJECT_CONTEXT.md` com o estado do change
