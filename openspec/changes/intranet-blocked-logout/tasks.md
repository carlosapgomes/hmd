# Tasks — intranet-blocked-logout

## Preflight

- [ ] Baseline: confirmar árvore limpa em `main`, registrar `BASE_REF` e rodar a suíte completa uma vez (`TEST_DB_PORT=55435 uv run pytest -q`)

## Implementação

- [ ] Slice 001 — logout no bloqueio + página de bloqueio com retorno ao login — `slices/slice-001-blocked-logout.md`

## Gate final

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy . && TEST_DB_PORT=55435 uv run pytest` — tudo verde, sem regressão vs. baseline
- [ ] `openspec validate intranet-blocked-logout --strict`

## Pós-gate (parent)

- [ ] Atualizar `PROJECT_CONTEXT.md` com o estado do change
