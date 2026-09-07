# Tasks: presidio-anonymization

> Execução slice a slice (worker + reviewer + parent commita, `/slice-loop`). Cada slice tem arquivo próprio em `slices/`.
> Pré-condição: changes 01–04 arquivados (specs `case-management` + `intake-nir` promovidas) ✓.
> Nota de ambiente: a dependência spaCy + modelo `pt_core_news_lg` (~541 MB) entra no lockfile — primeira sincronização é lenta.

## 0. Preflight

- [x] 0.1 Confirmar working tree limpa, registrar `BASE_REF`; baseline verde (gate do change 04 serve); `uv sync` com as deps novas resolve (após o slice 002) — árvore limpa, BASE_REF `32da09c`, baseline 337 válida; disco 406G livres p/ modelo

## 1. Pré-extração determinística

- [x] 1.1 Slice 001 — `deterministic.py` puro (nº ocorrência reusando padrões do intake, nome, nascimento, CPF/CNS por checksum futuro) + `DeterministicExtraction`. Ver `slices/slice-001-deterministic-extraction.md` (runs exatos 11/15 após fix de review)

## 2. Recognizers BR + engine

- [x] 2.1 Slice 002 — `recognizers.py` (BR_CPF/BR_CNS checksum, BR_CRM) + `engine.py` singleton (YAML, spaCy pt, threshold env) + deps pinadas. Ver `slices/slice-002-recognizers-engine.md` (presidio 2.2.364/spacy 3.8.16/pt-core-news-lg 3.8.0 por URL oficial; default_score_threshold no construtor)

## 3. Serviço de anonimização + artefatos

- [x] 3.1 Slice 003 — `PseudonymOperator` + merge determinístico×NLP + `anonymize_case_text` + campos do `Case` (anonymized_text/pseudonym_map/anonymization_report/patient_name/patient_birth_date) + evento. Ver `slices/slice-003-anonymize-service.md` (spike reprovou operador custom → componente próprio; chave canônica por categoria)

## 4. Worker/cluster

- [ ] 4.1 Slice 004 — cluster `anonymization` (ALT_CLUSTERS) + task idempotente com lock + signal de enqueue na entrada de ANONYMIZING + compose `worker-anonymization` + fail-closed + ADR-0007. Ver `slices/slice-004-worker-cluster.md`

## 5. Re-identificação + benchmark

- [ ] 5.1 Slice 005 — `reidentify_text` (roundtrip) + comando `anonymization_benchmark` (corpus sintético versionado, recall/p95/zero-PII, exit≠0 abaixo do mínimo). Ver `slices/slice-005-reidentify-benchmark.md`

## 6. Gate final do change

- [ ] 6.1 Quality gate completo (`uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`) + `openspec validate presidio-anonymization`; registrar resultado
- [ ] 6.2 Atualizar `PROJECT_CONTEXT.md` (estado pós-change; invariante anonymized_text; cluster anonymization; env novas) e preparar arquivamento
