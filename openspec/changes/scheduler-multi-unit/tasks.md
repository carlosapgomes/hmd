# Tasks: scheduler-multi-unit

Baseline: `beb9bcd` (admin-local-identity arquivado; 743 testes verdes).

- [x] 1. Preflight: árvore limpa; registrar `BASE_REF`; suíte completa uma única vez — BASE_REF `c0f6594`, 743 passed (2026-09-08)
- [x] 2.1 Slice 001 — campos de agendamento (migration 0008) + serviços confirmar/negar com resposta final ao NIR. Ver `slices/slice-001-scheduling-fields-services.md` — reviewer OK with notes (0 P0/0 P1; P2s: teste de atomicidade via validação concorrente, update_fields no deny cosmético), 145 testes locais verdes
- [x] 3.1 Slice 002 — transição `reopen_scheduling` + serviço de intercorrência (unidade 1 apenas). Ver `slices/slice-002-incident-reopen.md` — reviewer OK with notes (0 P0/0 P1; P2s: duplicação de helpers de teste, `scheduling_reopen_reason` não limpo na reconfirmação por design), 154 testes locais verdes
- [ ] 4.1 Slice 003 — app `scheduler`: fila completa, detalhe limitado (D3) e formulários. Ver `slices/slice-003-queue-detail-ui.md`
- [ ] 5.1 Gate final: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest` + `openspec validate scheduler-multi-unit`; registrar resultado
- [ ] 5.2 Atualizar `PROJECT_CONTEXT.md` (estado pós-change; spec `scheduling`) e preparar arquivamento
