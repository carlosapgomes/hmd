# Slice 003: Serviço de anonimização + artefatos no caso

## Objetivo

O coração do change: `PseudonymOperator` (tokens estáveis por caso), merge determinístico×NLP e o serviço `anonymize_case_text` — produz `anonymized_text` + `pseudonym_map` + `anonymization_report` (+ `patient_name`/`patient_birth_date`), persiste no caso e grava evento. Exceção propaga (fail-closed é fechado no worker do slice 004).

## Contexto necessário (contexto zero)

- Slices 001–002 entregues: `deterministic.py` e engine/recognizers.
- Change 03: `Case` (campos/migrations), `CaseEvent` + `apps/cases/events.py` (+`CASE_ANONYMIZATION_COMPLETED` a criar), transições.
- Pesquisa §5 (operators/`OperatorConfig`), §9 (pseudônimos estáveis por documento; mapa fora do prompt), §13 (recall > precisão; caveats de datas).
- Design: `../design.md` D5 (operador customizado com mapa), D6 (pipeline/merge/invariantes).
- Spec: Requirements "Pré-extração determinística" (cenário persistência — nome/nascimento), "Anonimização com pseudônimos estáveis" (3 cenários), "Fail-closed" (cenário é do worker, o serviço propaga).

## Requisitos

- **R1** `PseudonymOperator(Operator)` do presidio-anonymizer: instância por chamada; `operate(text, params)` devolve token da categoria (`PESSOA/CPF/CNS/CRM/DATA/LOCAL/ORGANIZACAO/TELEFONE/EMAIL/PII`) numerado por primeira ocorrência; expõe `mapping` (token → {value, entity_type}) após o uso; tokens no formato `<CATEGORIA_N>`.
- **R2** Merge determinístico×NLP em `apps/anonymization/services.py`: resultados do analyzer + spans garantidos da `DeterministicExtraction` (nome, nascimento como DATA, nº ocorrência como ID? **não** — nº de ocorrência É pseudonimizado? ver R3) — dedupe por sobreposição mantendo o span mais largo.
- **R3** Regra de escopo: TODAS as entidades do escopo (incluindo nome/nascimento/CPF/CNS determinísticos e o nº de ocorrência quando presente no corpo) são substituídas por tokens — o corpo anonimizado não contém nenhum identificador; o linkage usa os campos persistidos (D2), não o texto.
- **R4** `anonymize_case_text(case) -> AnonymizationResult`: pipeline D6 sobre `case.extracted_text`; grava em `case`: `anonymized_text`, `pseudonym_map` (JSON), `anonymization_report` (JSON: contagens por tipo, `ANONYMIZATION_SPACY_MODEL`, versões presidio, threshold), `patient_name`, `patient_birth_date` (e `agency_record_number` se determinístico achou e ainda vazio); cria `CaseEvent` `CASE_ANONYMIZATION_COMPLETED` (payload: contagens + versões); tudo numa transação. Retorna o resultado (task usa).
- **R5** Campos novos no `Case` + migration: `anonymized_text TextField blank`, `pseudonym_map JSONField default dict`, `anonymization_report JSONField default dict`, `patient_name CharField blank`, `patient_birth_date DateField null`; `+ CASE_ANONYMIZATION_COMPLETED` em `events.py`.
- **R6** Testes: texto sintético com 2 nomes distintos + CPF válido gerado repetido → tokens distintos/estáveis (repetição = mesmo token); nome que o NER não pega ainda assim tokenizado (span garantido); varredura zero-PII (regex CPF/CNS checksum + valores originais ausentes do output); relatório/evento presentes com contagens; texto vazio (`extracted_text=""`) → resultado com zero entidades e `anonymized_text=""` (sem erro — caso foi extraído vazio; gate deveria ter retido, mas serviço é defensivo).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/anonymization/operators.py` (ou services) | `test_services.py::test_stable_tokens_distinct_values`, `::test_repeated_value_same_token` |
| R2/R3 | `apps/anonymization/services.py` | `::test_deterministic_span_guaranteed_without_ner`, `::test_zero_pii_sweep` |
| R4/R5 | `apps/anonymization/services.py`, `apps/cases/models.py`, `apps/cases/migrations/000X_anonymization.py`, `apps/cases/events.py` | `::test_case_fields_and_event`, `::test_empty_text_defensive` |
| R6 | `apps/anonymization/tests/test_services.py` | `uv run pytest apps/anonymization/tests/test_services.py` |

## RED

- Comando: `uv run pytest apps/anonymization/tests/test_services.py`
- Falha esperada: `ImportError` (serviço/operador/campos inexistentes).

## GREEN / verificação local

- `uv run pytest apps/anonymization/tests/test_services.py` — exit 0
- `uv run pytest apps/anonymization/tests/ apps/cases/tests/` — exit 0
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0
- `uv run python manage.py makemigrations --check --dry-run` — sem drift

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/anonymization/{operators,services}.py
  - apps/anonymization/tests/test_services.py
  - apps/cases/models.py
  - apps/cases/migrations/000X_anonymization.py
  - apps/cases/events.py
allowed_incidental_files:
  - apps/anonymization/tests/conftest.py (extensões p/ casos sintéticos)
out_of_scope:
  - task/worker/FSM (004); re-identificação/benchmark (005); mudanças em intake
```

Escale ao parent se: a API de Operator do presidio-anonymizer 2.2.364 divergir (custom operator); sobreposições NER×determinístico exigirem política não prevista.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz (3 cenários da spec "Anonimização" + persistência da "Pré-extração" cobertos)
- [ ] Varredura zero-PII verde (regex checksum + valores originais ausentes)
- [ ] Mapa 1:1 (token↔valor) — pré-requisito do roundtrip do 005
- [ ] Gate parcial do slice verde
