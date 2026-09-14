# Tasks — intake-batch-semantics

## Preflight

- [x] Baseline: confirmar árvore limpa em `main`, registrar `BASE_REF` e rodar a suíte completa uma vez (`TEST_DB_PORT=55435 uv run pytest -q`)

## Implementação

- [x] Slice 001 — Serviços: lote + primitiva única + gate resubmit 1-PDF + reenvio corrigido + settings (rev. 2 pós-review: P0-1/P0-2) — `slices/slice-001-batch-service.md`
- [x] Slice 002 — Form/UI: tipo único, hints numéricos, anexos desabilitáveis, resultado do lote — `slices/slice-002-form-ui.md`
- [x] Slice 003 — UI do reenvio corrigido e do gate (hints/templates) — `slices/slice-003-corrected-resubmission.md`

## Gate final

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy . && TEST_DB_PORT=55435 uv run pytest` — tudo verde, sem regressão vs. baseline
- [ ] `openspec validate intake-batch-semantics --strict`

## Pós-gate (parent)

- [ ] Atualizar `PROJECT_CONTEXT.md` com o estado do change
