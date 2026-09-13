# Tasks — unit-labels-env

## Preflight

- [ ] Baseline: confirmar árvore limpa em `main`, registrar `BASE_REF` e rodar a suíte completa uma vez (`TEST_DB_PORT=55435 uv run pytest -q`)

## Implementação

- [ ] Slice 001 — núcleo: envs de labels + helper central + resposta final interpolada + choices do form — `slices/slice-001-labels-core.md`
- [ ] Slice 002 — superfície de exibição: scheduler views/presenters, dashboard, context processor, templates (manual + help text) — `slices/slice-002-labels-surface.md`

## Gate final

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy . && TEST_DB_PORT=55435 uv run pytest` — tudo verde, sem regressão vs. baseline
- [ ] `openspec validate unit-labels-env --strict`

## Pós-gate (parent)

- [ ] Atualizar `PROJECT_CONTEXT.md` com o estado do change
