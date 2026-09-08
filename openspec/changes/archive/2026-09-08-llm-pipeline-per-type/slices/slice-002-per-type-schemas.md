# Slice 002: Schemas por tipo (base + blocos + união)

## Objetivo

`apps/pipeline/schemas/` em Pydantic v2: modelo base comum do artefato LLM1, blocos específicos dos 13 tipos, composição **união** (`build_llm1_schema(types)`), resposta LLM2 e a normalização `oneOf→anyOf` para `response_format`. Puro — sem LLM, sem DB.

## Contexto necessário (contexto zero)

- Plano §7 (schemas: base comum + blocos específicos + união p/ multiprocedimento; evidence/status obrigatórios; nunca completar ausência).
- Referência: `/projects/dev/ats-web/apps/pipeline/schemas/llm1_v2.py` (`Llm1ResponseV2`, `requested_procedures` com `evidence_spans`, `StrictModel`, `Llm1ProcedureEvidenceSpanV2`) e `llm2_v2.py` (SOMENTE-LEITURA — adaptar de 2 tipos para 13). `adapters.py` do ats-web tem a normalização oneOf→anyOf.
- Catálogo HMD: `apps/cases/procedure_catalog.py` (13 tipos; blocos específicos citados no plano: angio_fav→estado do acesso; filtro_cava→TEp/contraindicação anticoagulação; permicath→infecção ativa — os demais blocos seguem a seção clínica do tipo em `temp/parametrosHMD.md`).
- Medicamentos NÃO são PII (change 05 não os tokeniza) — nomes de fármacos aparecem no artefato como texto.

## Requisitos

- **R1** `StrictModel` (pydantic v2, `extra="forbid"`). `Llm1EvidenceSpan` (field_path + excerpt) e `status ∈ {confirmado, nao_informado, incerto}` como tipo compartilhado.
- **R2** Base comum (`Llm1CaseBase`): pedido (procedimentos como labels/tokens), contexto clínico, linha do tempo, exames com resultados objetivos (valor+unidade+evidence+status), medicações (nome+classe anticoagulante/antiagregante quando houver), comorbidades, contraindicações, `trechos_nao_classificados` — campos clínicos com evidence obrigatória (validator: campo preenchido sem evidence → erro de validação).
- **R3** Blocos específicos por tipo: **apenas os três nomeados no plano** (`angio_fav` → estado do acesso; `filtro_cava` → TEp/contraindicação anticoagulação; `permicath` → infecção ativa); os demais 10 tipos usam somente a base comum (correção do review: nada de campos inventados fora do parametrosHMD; blocos são informativos ao LLM).
- **R4** `build_llm1_schema(types: Sequence[str]) -> type[BaseModel]`: dinâmico com base + união dos blocos dos `types` (validados contra o catálogo; duplicata → erro). **O campo `pedido.procedimentos_solicitados` aceita QUALQUER tipo dos 13** (Literal do catálogo completo — detecção não restringida aos declarados; correção do review).
- **R5** `Llm2Response`: `summary_text` + `procedures[]` (tipo + sugestão `aceitar|recusar` + motivos[] + evidence) — valida a saída da sumarização.
- **R6** `normalize_schema_for_response_format(model) -> dict`: JSON schema com **`oneOf→anyOf` + `required` de todos os campos + `additionalProperties: false`** em todos os níveis (requisito do modo strict; herança completa do adapter do ats-web).
- **R7** Testes: base rejeita extra fields; campo clínico sem evidence rejeitado; status inválido rejeitado; união com 1 tipo e com 3 tipos (fields dos 3 presentes); tipo fora do catálogo rejeitado; normalização oneOf→anyOf idempotente; round-trip parse de payload JSON válido de exemplo (fixtures em código, sem PII real — valores clínicos fictícios).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1/R2 | `apps/pipeline/schemas/base.py` | `test_schemas.py::test_strict_extra_forbidden`, `::test_clinical_field_requires_evidence`, `::test_status_enum` |
| R3 | `apps/pipeline/schemas/blocks.py` | `::test_specific_blocks_per_type` (parameterized 13) |
| R4 | `apps/pipeline/schemas/__init__.py` | `::test_union_single`, `::test_union_three_types`, `::test_unknown_type_rejected` |
| R5 | `apps/pipeline/schemas/llm2.py` | `::test_llm2_response_valid` |
| R6 | `apps/pipeline/schemas/adapters.py` | `::test_oneof_to_anyof`, `::test_required_and_additional_properties_strict` |
| R7 | `apps/pipeline/tests/test_schemas.py` | `uv run pytest apps/pipeline/tests/test_schemas.py` |

## RED

- Comando: `uv run pytest apps/pipeline/tests/test_schemas.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.pipeline.schemas'`.

## GREEN / verificação local

- `uv run pytest apps/pipeline/tests/test_schemas.py` — exit 0
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/pipeline/schemas/{__init__,base,blocks,llm2,adapters}.py
  - apps/pipeline/tests/test_schemas.py
allowed_incidental_files: []
out_of_scope:
  - prompts (003); serviços/LLM (004+); policy (005); orchestrator (006); models
```

Escale ao parent se: um tipo do catálogo não tiver campo específico clinicamente defensável (documentar bloco vazio em vez de inventar).

## Critérios de aceitação

- [ ] R1–R7 comprovados; união gera UM modelo válido para N tipos
- [ ] Evidence obrigatória enforcement por validator
- [ ] Gate parcial do slice verde
