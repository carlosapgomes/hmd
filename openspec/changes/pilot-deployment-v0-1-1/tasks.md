# Tasks: pilot-deployment-v0-1-1

Baseline: `cde28ff`; `BASE_REF = cde28ff`; suíte ×1 verde (1037).

- [x] 1. Preflight: árvore limpa; `BASE_REF = cde28ff`; suíte verde ×1 (1037)
- [x] 2.1 Slice 001 — runtime prod: gunicorn/CMD/collectstatic no Dockerfile (imagem buildada e validada: manifest.json + gunicorn 23.0.0 no container), /healthz+/readyz (readyz também valida o cache hmd_cache — P2 F2), settings prod (DatabaseCache hmd_cache, CSRF/PROXY/HOSTS; guard com mensagem atualizada). Review: sem P0/P1; P2s F1/F2/F4/F5 fechados pelo pai (mensagem do guard, cache no readyz, require_safe, duplicata removida), F3 (URL com barra no healthcheck) e nota de TRUSTED_PROXY_HEADER/Caddy incorporados ao slice 003. Desvio autorizado: test_lockout.py atualizado ao novo invariante de cache (decisão A). Worker atingiu timeout no build opcional; código completo e verificado pelo pai
- [ ] 2.2 Slice 002 — INTAKE_ENABLED fail-closed (serviços+views+tests). Ver `slices/slice-002-intake-lock.md`
- [ ] 2.3 Slice 003 — docker-compose.prod.yml (redes externas/aliases/shared-PG/profiles migrate+workers/secrets por arquivo/healthcheck/rotlogs) + .env.example + README piloto. Ver `slices/slice-003-compose-prod.md`
- [ ] 2.4 Slice 004 — workflow GHCR amd64 + CHANGELOG v0.1.1 + bump pyproject. Ver `slices/slice-004-release.md`
- [ ] 3.1 Gate final: ruff/format/mypy/pytest + `docker compose -f docker-compose.prod.yml config --quiet` + `openspec validate pilot-deployment-v0-1-1 --strict`; registrar resultado
- [ ] 4.1 Review independente final (deploy-readiness) → push main + tag v0.1.1 + release GHCR + digest reportado (somente após review OK)
