# Tasks: doctor-queue-decision

Baseline: `40f5256` (change 06 arquivado; 651 testes verdes).

- [x] 1. Preflight: árvore limpa; registrar `BASE_REF`; suíte completa uma única vez — BASE_REF `9e7eb3a`, 651 passed (2026-09-08)
- [x] 2.1 Slice 001 — `DoctorSpecialty` + `User.specialties` M2M (seed congelado na migration 0003) + helper. Ver `slices/slice-001-doctor-specialties.md` (invariante DB==catálogo por teste; P2: subtipos 1×/request no slice 002)
- [ ] 3.1 Slice 002 — app `doctor`: fila por estado com filtro de subtipo + access control. Ver `slices/slice-002-queue-access-control.md`
- [ ] 4.1 Slice 003 — presenter re-identificado + `reidentify_structure` + PDF original + prior-case real. Ver `slices/slice-003-presenter-reidentified.md`
- [ ] 5.1 Slice 004 — formulário de decisão por procedimento + submit atômico + caso decidido. Ver `slices/slice-004-decision-submit.md`
- [ ] 6.1 Gate final: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest` + `openspec validate doctor-queue-decision`; registrar resultado
- [ ] 6.2 Atualizar `PROJECT_CONTEXT.md` (estado pós-change; nova spec `doctor-decision`) e preparar arquivamento
