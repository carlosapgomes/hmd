# Tasks: admin-local-identity

Baseline: `c2b0635` (change 07 arquivado; 730 testes verdes).

- [x] 1. Preflight: árvore limpa; registrar `BASE_REF`; suíte completa uma única vez — BASE_REF `610b90b`, 730 passed (2026-09-08)
- [x] 2.1 Slice 001 — admin local por design: remoção do gate + extinção da flag + anti-lockout no Django admin + ADR-0009. Ver `slices/slice-001-admin-local-auth.md` (P2s fechados pelo parent: docstrings views.py, bullet ADRs README, teste de normalização; P2s registrados: replicação do contexto do form no pre-check, `_wrapped` privado no teste de wiring, ADR-0004 histórico)
- [x] 3.1 Gate final: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest` + `openspec validate admin-local-identity`; registrar resultado — **743 passed, ruff/format/mypy limpos, `openspec validate --strict` valid (2026-09-08)**
- [x] 3.2 Atualizar `PROJECT_CONTEXT.md` (emenda ADR-0009; flag extinta) e preparar arquivamento
