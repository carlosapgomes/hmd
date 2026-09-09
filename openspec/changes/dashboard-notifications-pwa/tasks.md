# Tasks: dashboard-notifications-pwa

Baseline: `4f756cf` (change 10 arquivado; 940 testes verdes ×2 rodadas).

- [ ] 1. Preflight: árvore limpa; registrar `BASE_REF`; suíte completa uma única vez
- [ ] 2.1 Slice 001 — `UserNotification` (model+migration) + services + signal de marcos (idempotente, fan-out schedulers, fail-safe). Ver `slices/slice-001-notification-core.md`
- [ ] 2.2 Slice 002 — UI de notificações: badge/context processor, lista com janela, abrir+redirect por papel, marcar-todas, endpoint JSON. Ver `slices/slice-002-notification-ui.md`
- [ ] 3.1 Slice 003 — app `apps/dashboard`: métricas por período/tipo/unidade (fontes imutáveis, zero-PHI) + view/template + navbar. Ver `slices/slice-003-dashboard.md`
- [ ] 4.1 Slice 004 — PWA: ícones HMD (script+assets), manifest, service worker, wiring `base.html`. Ver `slices/slice-004-pwa.md`
- [ ] 5.1 Slice 005 — manual de usuário por papel + navbar + delta MODIFIED case-management verificado. Ver `slices/slice-005-manual.md`
- [ ] 6.1 Gate final: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest` + `openspec validate dashboard-notifications-pwa --strict`; registrar resultado
- [ ] 6.2 Atualizar `PROJECT_CONTEXT.md` (estado pós-change; specs novas + case-management MODIFIED) e preparar arquivamento
