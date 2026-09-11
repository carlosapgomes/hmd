# Tasks: pilot-deployment-v0-1-1

Baseline: `5554ba0` (v0.1.0 sanitizado; 1037 testes verdes).

- [ ] 1. Preflight: árvore limpa; registrar `BASE_REF`; suíte completa uma única vez
- [ ] 2.1 Slice 001 — runtime prod: gunicorn+CMD+collectstatic no Dockerfile, /healthz+/readyz, settings prod (DatabaseCache hmd_cache, CSRF/PROXY/HOSTS). Ver `slices/slice-001-runtime.md`
- [ ] 2.2 Slice 002 — INTAKE_ENABLED fail-closed (serviços+views+tests). Ver `slices/slice-002-intake-lock.md`
- [ ] 2.3 Slice 003 — docker-compose.prod.yml (redes externas/aliases/shared-PG/profiles migrate+workers/secrets por arquivo/healthcheck/rotlogs) + .env.example + README piloto. Ver `slices/slice-003-compose-prod.md`
- [ ] 2.4 Slice 004 — workflow GHCR amd64 + CHANGELOG v0.1.1 + bump pyproject. Ver `slices/slice-004-release.md`
- [ ] 3.1 Gate final: ruff/format/mypy/pytest + `docker compose -f docker-compose.prod.yml config --quiet` + `openspec validate pilot-deployment-v0-1-1 --strict`; registrar resultado
- [ ] 4.1 Review independente final (deploy-readiness) → push main + tag v0.1.1 + release GHCR + digest reportado (somente após review OK)
