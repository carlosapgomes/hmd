# Tasks: case-core-fsm-procedures

> Execução slice a slice (worker + reviewer + parent commita, `/slice-loop`). Cada slice tem arquivo próprio em `slices/`.
> **D1 confirmada pelo dono (2026-09-07): `django-fsm-2==4.2.4`** (MIT).

## 0. Preflight

- [ ] 0.1 Confirmar working tree limpa, registrar `BASE_REF`; baseline verde uma vez (gate do change 02 serve)
- [x] 0.2 Dono confirma D1 — **django-fsm-2 confirmado (2026-09-07)**; plano §3/§4/§11, PROJECT_CONTEXT e ADR-0001 emendados; ADR-0005 (novo) nasce no slice 002

## 1. Catálogo

- [x] 1.1 Slice 001 — `procedure_catalog.py`: 13 `ProcedureProfile` + `CRITERIA_SECTIONS` (S1–S8) + fail-fast + comando de verificação idempotente. Ver `slices/slice-001-procedure-catalog.md`

## 2. Case + FSM + eventos

- [x] 2.1 Slice 002 — `Case` (17 estados, transições protegidas, `_record_event`) + `CaseEvent` append-only + migration + dependência FSM. Ver `slices/slice-002-case-fsm-events.md` (gravação direta de eventos no mesmo atomic — D5)

## 3. Procedimentos por caso

- [x] 3.1 Slice 003 — `CaseProcedure` (neutro, uniq caso+tipo) + serviços de declaração/detecção/decisão atômicos com eventos. Ver `slices/slice-003-case-procedures-services.md` (validação de catálogo pré-mutação + defesa em save())

## 4. Locks

- [ ] 4.1 Slice 004 — campos de lock/lease + `locks.py` (claim/assert/release/renew/expire com token e eventos). Ver `slices/slice-004-case-locks.md`

## 5. Comunicações

- [ ] 5.1 Slice 005 — `CaseCommunicationMessage` (user/system) + projeção sistêmica via signal + serviço de post do usuário. Ver `slices/slice-005-communications.md`

## 6. Gate final do change

- [ ] 6.1 Quality gate completo (`uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`) + `openspec validate case-core-fsm-procedures`; registrar resultado
- [ ] 6.2 Atualizar `PROJECT_CONTEXT.md` (estado pós-change; novos contratos: estados do catálogo/FSM como guardrails) e preparar arquivamento
