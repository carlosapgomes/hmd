# Tasks — image-hardening-v0-1-4

## Preflight

- [x] Baseline: confirmar árvore limpa em `main`, registrar `BASE_REF` e rodar a suíte completa uma vez (`TEST_DB_PORT=55435 uv run pytest -q`)

## Implementação

- [x] Slice 001 — WSGI fail-safe: default `config.settings.prod` — `slices/slice-001-wsgi-default-prod.md`
- [x] Slice 002 — Imagem não-root: usuário 10001 + mídia com ownership — `slices/slice-002-image-non-root.md`
- [x] Slice 003 — Release v0.1.4: bump, CHANGELOG, compose default, pins do README — `slices/slice-003-release-v0-1-4.md`

## Gate final

- [x] `uv run ruff check . && uv run ruff format --check . && uv run mypy . && TEST_DB_PORT=55435 uv run pytest` — tudo verde, sem regressão vs. baseline
- [x] Parent: build da imagem + smoke não-root (design D3) — boot gunicorn uid 10001, `/healthz` 200, `manage.py check` prod no container
- [x] `openspec validate image-hardening-v0-1-4 --strict`

## Pós-gate (parent)

- [ ] Atualizar `PROJECT_CONTEXT.md` com o estado do change
- [ ] (Owner-gated) tag `v0.1.4` + push + publicação GHCR + registro do digest; re-pin do deploy
