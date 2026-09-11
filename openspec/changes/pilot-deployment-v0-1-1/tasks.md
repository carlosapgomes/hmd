# Tasks: pilot-deployment-v0-1-1

Baseline: `cde28ff`; `BASE_REF = cde28ff`; suíte ×1 verde (1037).

- [x] 1. Preflight: árvore limpa; `BASE_REF = cde28ff`; suíte verde ×1 (1037)
- [x] 2.1 Slice 001 — runtime prod: gunicorn/CMD/collectstatic no Dockerfile (imagem buildada e validada: manifest.json + gunicorn 23.0.0 no container), /healthz+/readyz (readyz também valida o cache hmd_cache — P2 F2), settings prod (DatabaseCache hmd_cache, CSRF/PROXY/HOSTS; guard com mensagem atualizada). Review: sem P0/P1; P2s F1/F2/F4/F5 fechados pelo pai (mensagem do guard, cache no readyz, require_safe, duplicata removida), F3 (URL com barra no healthcheck) e nota de TRUSTED_PROXY_HEADER/Caddy incorporados ao slice 003. Desvio autorizado: test_lockout.py atualizado ao novo invariante de cache (decisão A). Worker atingiu timeout no build opcional; código completo e verificado pelo pai
- [x] 2.2 Slice 002 — INTAKE_ENABLED fail-closed (serviços+views+tests). Review: P1-1 fechado pelo pai (INTAKE_ENABLED=True pinado em test.py — suíte determinística com env host false, reprodução verde); P2-2 (linha do .env.example comentada — cópia p/ prod não reabre upload), P2-4 (check do reenvio corrigido ANTES do estado — mensagem de desabilitado prevalece; aplica-se a GET+POST dessa rota), P2-5 (docstring) fechados; P2-3 (gate_release fora do bloqueio) aceito por design (fase 1 sem casos retidos; out_of_scope declarado). Desvios aceitos: IntakeValidationError (ValueError nomeado), gate_resubmit desligado → 302
- [ ] 2.3 Slice 003 — docker-compose.prod.yml (redes externas/aliases/shared-PG/profiles migrate+workers/secrets por arquivo/healthcheck/rotlogs) + .env.example + README piloto. Ver `slices/slice-003-compose-prod.md`
- [ ] 2.4 Slice 004 — workflow GHCR amd64 + CHANGELOG v0.1.1 + bump pyproject. Ver `slices/slice-004-release.md`
- [ ] 3.1 Gate final: ruff/format/mypy/pytest + `docker compose -f docker-compose.prod.yml config --quiet` + `openspec validate pilot-deployment-v0-1-1 --strict`; registrar resultado
- [ ] 4.1 Review independente final (deploy-readiness) → push main + tag v0.1.1 + release GHCR + digest reportado (somente após review OK)
