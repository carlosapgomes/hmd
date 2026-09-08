# Tasks: admin-local-identity

Baseline: `c2b0635` (change 07 arquivado; 730 testes verdes).

- [ ] 1. Preflight: árvore limpa; registrar `BASE_REF`; suíte completa uma única vez
- [ ] 2.1 Slice 001 — admin local por design: remoção do gate + extinção da flag + anti-lockout no Django admin + ADR-0009. Ver `slices/slice-001-admin-local-auth.md`
- [ ] 3.1 Gate final: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest` + `openspec validate admin-local-identity`; registrar resultado
- [ ] 3.2 Atualizar `PROJECT_CONTEXT.md` (emenda ADR-0009; flag extinta) e preparar arquivamento
