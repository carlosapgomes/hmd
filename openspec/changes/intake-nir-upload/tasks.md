# Tasks: intake-nir-upload

> Execução slice a slice (worker + reviewer + parent commita, `/slice-loop`). Cada slice tem arquivo próprio em `slices/`.
> Pré-condição: change `case-core-fsm-procedures` arquivado (specs `case-management` promovidas) ✓.

## 0. Preflight

- [x] 0.1 Confirmar working tree limpa, registrar `BASE_REF`; baseline verde (gate do change 03 serve) — árvore limpa, BASE_REF `c3ac522`, baseline 277 testes válida

## 1. Criação do caso (upload + declaração)

- [x] 1.1 Slice 001 — `CaseDocument` + campos novos do `Case` + `create_case_with_documents` atômico + form/view/template do upload com multi-select de tipos. Ver `slices/slice-001-case-documents-creation.md`

## 2. Extração e gate (funções puras)

- [x] 2.1 Slice 002 — `pdf_utils` (PyMuPDF, watermark, nº ocorrência) + `regulation_gate` adaptado — funções puras com fixtures geradas em teste. Ver `slices/slice-002-pdf-gate-utils.md` (pymupdf 1.28.2, nº do bruto antes do strip)

## 3. Worker/cluster

- [ ] 3.1 Slice 003 — django-q2 (cluster `pdf`, `INTAKE_RUN_TASKS_INLINE`) + task `process_case_documents` (lock, FSM, gate, nº) + compose `worker-pdf`/media + env. Ver `slices/slice-003-worker-task-q2.md`

## 4. Meus casos e detalhe

- [ ] 4.1 Slice 004 — `my_cases` (escopo por criador, flag de retenção) + `case_detail` (documentos servidos, trilha, comunicações). Ver `slices/slice-004-my-cases-detail.md`

## 5. Revisão do gate

- [ ] 5.1 Slice 005 — ações `gate_release` (bypass com evento) e `gate_resubmit` (substitui docs e reprocessa). Ver `slices/slice-005-gate-review-actions.md`

## 6. Gate final do change

- [ ] 6.1 Quality gate completo (`uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`) + `openspec validate intake-nir-upload`; registrar resultado
- [ ] 6.2 Atualizar `PROJECT_CONTEXT.md` (estado pós-change; cluster pdf; env novas) e preparar arquivamento
