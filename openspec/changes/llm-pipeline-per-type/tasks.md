# Tasks: llm-pipeline-per-type

> Execução slice a slice (worker + reviewer + parent commita, `/slice-loop`). Cada slice tem arquivo próprio em `slices/`.
> Pré-condição: changes 01–05 arquivados (specs `anonymization`/`intake-nir`/`case-management` promovidas) ✓.

## 0. Preflight

- [ ] 0.1 Confirmar working tree limpa, registrar `BASE_REF`; baseline verde (gate do change 05 serve; 399 testes)

## 1. Cliente OpenRouter

- [ ] 1.1 Slice 001 — cliente (SDK OpenAI, transport injetável, erros tipados, envs) + `manage.py llm_check` manual. Ver `slices/slice-001-openrouter-client.md`

## 2. Schemas por tipo

- [ ] 2.1 Slice 002 — Pydantic v2: base comum + 13 blocos específicos + composição união + evidence/status + normalização oneOf→anyOf. Ver `slices/slice-002-per-type-schemas.md`

## 3. Prompts versionados

- [ ] 3.1 Slice 003 — `apps/llm`: PromptTemplate + seeds idempotentes (2 system neutros + 26 user por tipo) + montagem por caso. Ver `slices/slice-003-prompt-templates.md`

## 4. LLM1 + reconciliação + gate de divergência

- [ ] 4.1 Slice 004 — `llm1_service` com guardas + reconciliação com upsert de detecção + retenção por divergência + transição `bypass_pipeline_divergence` + extensão do gate_release + **migration única (4 campos) e todos os eventos do change**. Ver `slices/slice-004-llm1-reconciliation.md`

## 5. Policy + prior-case

- [ ] 5.1 Slice 005 — policy determinística consultiva (S1–S8 + requisitos gerais; ok|alerta|nao_informado) + prior-case (nº 7d + fallback nome/nascimento 15d) com eventos. Ver `slices/slice-005-policy-priorcase.md`

## 6. LLM2 + orquestrador + cluster llm

- [ ] 6.1 Slice 006 — `llm2_service` (visão filtrada, policy prevalece, strictest) + orquestrador/task (trigger duplo, retomada pós-bypass, lock, fail-closed) + worker-llm + ADR-0008. Ver `slices/slice-006-llm2-orchestrator.md`

## 7. Gate final do change

- [ ] 7.1 Quality gate completo (`uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`) + `openspec validate llm-pipeline-per-type`; registrar resultado
- [ ] 7.2 Atualizar `PROJECT_CONTEXT.md` (estado pós-change; invariante "LLM só vê tokens"; envs novas; pendências operacionais: benchmark de modelos + corpus real) e preparar arquivamento
