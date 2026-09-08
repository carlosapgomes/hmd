# Tasks: nir-result-closure

Baseline: `1e9cf87` (change 08 arquivado; 806 testes verdes ×2 rodadas).

- [ ] 1. Preflight: árvore limpa; registrar `BASE_REF`; suíte completa uma única vez
- [ ] 2.1 Slice 001 — resposta final de negativa médica (serviço `post_doctor_denial_reply` + wiring na view de decisão). Ver `slices/slice-001-denial-final-reply.md`
- [ ] 2.2 Slice 002 — ciência do NIR + limpeza transacional (`acknowledge_case_receipt`; minimização D2; regressão prior-case). Ver `slices/slice-002-ack-cleanup.md`
- [ ] 3.1 Slice 003 — UI de fechamento do NIR (resultado, ciência, aba encerrados). Ver `slices/slice-003-nir-ui.md`
- [ ] 4.1 Slice 004 — reenvio corrigido (migration 0009 + `create_corrected_resubmission` + UI). Ver `slices/slice-004-corrected-resubmission.md`
- [ ] 5.1 Gate final: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest` + `openspec validate nir-result-closure`; registrar resultado
- [ ] 5.2 Atualizar `PROJECT_CONTEXT.md` (estado pós-change; spec `case-closure`) e preparar arquivamento
