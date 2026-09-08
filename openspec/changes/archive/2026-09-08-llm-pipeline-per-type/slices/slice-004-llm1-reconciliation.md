# Slice 004: LLM1 com guardas + reconciliação + gate de divergência

## Objetivo

A extração estruturada e o controle de escopo: `llm1_service` (guardas strict/language/retry-único/fail-closed; persiste `structured_data` com evento enxuto), reconciliação declarado×detectado (atualiza rows via `set_detected_procedures`), retenção por divergência em `LLM_EXTRACTING` (flag+motivo+evento) e a **nova transição** `bypass_pipeline_divergence` + extensão do `gate_release` do intake para despachar pela retenção.

## Contexto necessário (contexto zero)

- Slices 001–003 entregues: cliente (factory injetável, `LlmError`), schemas (união), prompts (montagem por tipos).
- Change 03: `set_detected_procedures(case, detection, *, user, role)`; transições com `*, user, role`; `events.py`; `manual_review_required/reason` (campos do Case, usados pelo gate do change 04).
- Change 04: `apps/intake/services.py::release_retained_case` (hoje hardcode `PDF_EXTRACTING`+`complete_pdf_extraction`) e views de gate — **extensão mínima autorizada pelo design D5**.
- Referências ats-web (SOMENTE-LEITURA): `apps/pipeline/{llm1_service_v2,json_parser,ptbr_language_guard,procedure_reconciliation}.py`.
- Plano §7: guardas (strict JSON/oneOf→anyOf, language guard pt-BR, máx 1 retry corretivo tipado, fail-closed); §4/§7: gate de revisão NIR p/ divergência.

## Requisitos

- **R1** `Case`: **migration ÚNICA deste change criada aqui** com os 4 campos (`structured_data`, `summary_text`, `suggested_action`, `policy_result`, todos com defaults) — dono definido (correção do review; 005/006 apenas usam). **`events.py` também fica completo aqui**: `CASE_LLM1_COMPLETED`, `CASE_LLM2_COMPLETED`, `CASE_GATE_PROCEDURE_DIVERGENCE`, `CASE_POLICY_EVALUATED`, `PRIOR_CASE_LOOKUP` (+ reuso de `CASE_GATE_BYPASSED`).
- **R2** `run_llm1_extraction(case, *, user=None, role="system")`: schema união dos **declarados** (`get_declared_procedure_types`); prompts (003) com `{texto_anonimizado}` = `case.anonymized_text`; chamada via factory com `response_format=normalize_schema_for_response_format(schema)`; parse (`json_parser` tolerante) → validação strict; **language guard pt-BR** (lista canônica versionada de marcadores de outro idioma nos campos textuais — comportamento real do ats-web, não proporção); **retry corretivo único** (mensagem tipada por falha: schema/idioma; **a resposta inválida anterior NÃO é reenviada**); esgotado → levanta `LlmPipelineError("llm1_<motivo>")` tipado — **o orquestrador é o único dono de `fail_processing`** (serviços nunca transicionam); sucesso → persiste `structured_data` (**só tokens** — assert de sanidade: nenhum valor do `pseudonym_map` no artefato serializado) + evento `CASE_LLM1_COMPLETED` (prompt names+versions, tipos, contagens).
- **R3** `reconcile_procedures(declared, detected) -> ReconciliationResult` puro: por tipo `match | missing_declaration | not_detected`.
- **R4** Integração de detecção: `detected = tipos em structured_data.pedido.procedimentos_solicitados` (qualquer tipo do catálogo — schema do slice 002) → **novo serviço `record_detected_procedures(case, detection, *, user, role)`** em `apps/cases/procedures.py`: upsert atômico (cria rows `declared_by_nir=False` para detectados não-declarados; atualiza `detection_status` de todas) + evento `CASE_PROCEDURES_DETECTED` (o `set_detected_procedures` do change 03 rejeita tipos sem row — insuficiente p/ divergência; correção do review).
- **R5** Divergência (`missing_declaration` ∪ `not_detected` não-vazio) → grava `manual_review_required=True` + `manual_review_reason="procedure_divergence"` + evento `CASE_GATE_PROCEDURE_DIVERGENCE` (payload: classificação por tipo) — **sem** transição de saída (permanece `LLM_EXTRACTING`). Sem divergência → caller avança com `complete_llm_extraction`.
- **R6** FSM: **nova transição** `bypass_pipeline_divergence` (`source=LLM_EXTRACTING`, `target=LLM_SUMMARIZING`, protected, `*, user, role`, evento `CASE_GATE_BYPASSED` com payload `reason=procedure_divergence`); atualizada a docstring/tabela do modelo. **Primeira transição adicionada pós-change-03** — guardrail respeitado (transições sim, estados não).
- **R7** Intake `release_retained_case`: despacha por (estado, razão): `PDF_EXTRACTING` → comportamento atual (formato); `LLM_EXTRACTING` + `manual_review_reason="procedure_divergence"` → `bypass_pipeline_divergence` (mesma transação; zera flag; evento de bypass); demais → erro 400 como hoje. Views/templates do gate passam a listar casos retidos nos DOIS estados (badge distinto); a ação de **reenviar documentos** continua só para retenção de formato.
- **R8** Testes: extração feliz (fake client retorna JSON válido) → artefato+evento; inválida→retry→inválida → fail-processing chamado com motivo; language guard rejeita EN; retry ok na 2ª; assert de tokens (valor do mapa nunca aparece); reconcile puro (match/missing/not_detected); divergência retém (flag+evento, SEM transição); coincidência → rows atualizadas; bypass via intake service (LLM_EXTRACTING→LLM_SUMMARIZING com evento, flag zerada); release de caso não-retido → 400; `bypass_pipeline_divergence` de estado errado → TransitionNotAllowed.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/cases/models.py`, migration | (implícito nos testes de serviço) |
| R2 | `apps/pipeline/{llm1_service,json_parser,ptbr_language_guard}.py` | `test_llm1.py::test_happy_persists_tokens_only`, `::test_invalid_then_retry_then_failed`, `::test_language_guard_rejects_english`, `::test_retry_succeeds` |
| R3 | `apps/pipeline/procedure_reconciliation.py` | `test_reconciliation.py::test_classification` |
| R4 | `apps/cases/procedures.py` (+record_detected) | `test_llm1.py::test_upsert_creates_undeclared_rows` |
| R5 | `apps/pipeline/llm1_service.py` | `test_llm1.py::test_divergence_retains`, `::test_match_updates_rows` |
| R6 | `apps/cases/models.py` (+events) | `test_llm1.py::test_bypass_transition`, `::test_bypass_wrong_state_raises` |
| R7 | `apps/intake/{services,views}.py`, `templates/intake/*` | `test_gate_divergence.py::test_release_dispatches_by_retention`, `::test_release_format_unchanged` |
| R8 | `apps/pipeline/tests/`, `apps/intake/tests/test_gate_divergence.py` | `uv run pytest apps/pipeline/tests/test_llm1.py apps/pipeline/tests/test_reconciliation.py apps/intake/tests/test_gate_divergence.py` |

## RED

- Comando: `uv run pytest apps/pipeline/tests/test_llm1.py`
- Falha esperada: `ModuleNotFoundError: apps.pipeline.llm1_service`.

## GREEN / verificação local

- Os 3 arquivos de teste acima — exit 0
- `uv run pytest apps/pipeline/tests/ apps/intake/tests/ apps/cases/tests/` — exit 0
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .`; `makemigrations --check` — sem drift

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/pipeline/{llm1_service,json_parser,ptbr_language_guard,procedure_reconciliation}.py
  - apps/pipeline/tests/{test_llm1,test_reconciliation}.py
  - apps/cases/models.py            # +4 campos + transição
  - apps/cases/migrations/000X_llm_artifacts.py
  - apps/cases/events.py            # +TODOS os 5 eventos novos do change (dono único deste slice)
  - apps/intake/{services,views}.py # despacho do release
  - apps/intake/tests/test_gate_divergence.py
  - templates/intake/{my_cases,case_detail}.html  # badge/lista das 2 retenções
out_of_scope:
  - policy/prior-case (005); LLM2/orchestrator (006); alterar gate de FORMATO (comportamento intacto); novos estados
```

Escale ao parent se: a extensão do intake passar de despacho+badge (refactor de views); o assert de tokens revelar vazamento real (escalar imediatamente).

## Critérios de aceitação

- [ ] R1–R8 comprovados (guardas: strict, idioma, retry único, fail-closed)
- [ ] Divergência nunca avança sozinha; bypass auditado com declarado preservado
- [ ] Artefato 100% tokens (assert comprovado)
- [ ] Gate parcial do slice verde
