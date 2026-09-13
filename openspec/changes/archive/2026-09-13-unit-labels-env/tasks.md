# Tasks — unit-labels-env

## Preflight

- [x] Baseline: confirmar árvore limpa em `main`, registrar `BASE_REF` e rodar a suíte completa uma vez (`TEST_DB_PORT=55435 uv run pytest -q`)

## Implementação

- [x] Slice 001 — núcleo: envs de labels + helper central + resposta final interpolada + choices do form — `slices/slice-001-labels-core.md`
- [x] Slice 002 — superfície de exibição: scheduler views/presenters, dashboard, context processor, templates (manual + help text) — `slices/slice-002-labels-surface.md`

## Gate final

- [x] `uv run ruff check . && uv run ruff format --check . && uv run mypy . && TEST_DB_PORT=55435 uv run pytest` — tudo verde, sem regressão vs. baseline
- [x] `openspec validate unit-labels-env --strict`

## Pós-gate (parent)

- [x] Atualizar `PROJECT_CONTEXT.md` com o estado do change
