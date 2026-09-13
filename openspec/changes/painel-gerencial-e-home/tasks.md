# Tasks — painel-gerencial-e-home

## Preflight

- [x] Baseline: confirmar árvore limpa em `main`, registrar `BASE_REF` e rodar a suíte completa uma vez (`TEST_DB_PORT=55435 uv run pytest -q`)

## Implementação

- [x] Slice 001 — Painel exclusivo manager/admin (rota 403 + navbar) — `slices/slice-001-painel-gate.md`
- [ ] Slice 002 — Home por papel ativo + ordem do menu NIR — `slices/slice-002-home-por-papel.md`

## Gate final

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy . && TEST_DB_PORT=55435 uv run pytest` — tudo verde, sem regressão vs. baseline
- [ ] `openspec validate painel-gerencial-e-home --strict`

## Pós-gate (parent)

- [ ] Atualizar `PROJECT_CONTEXT.md` com o estado do change
