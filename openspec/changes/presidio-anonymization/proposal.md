# Proposal: presidio-anonymization

## Why

O dono fixou o invariante de privacidade central do HMD: **nenhuma chamada LLM vê PII** — o texto extraído do relatório (change 04, `Case.extracted_text`) contém nome, CPF/CNS, CRM, datas e locais, e sai do hospital via OpenRouter. Este change entrega a barreira: pré-extração determinística dos identificadores de linkage, anonimização do corpo com Presidio pt-BR + recognizers brasileiros (checksum CPF/CNS), pseudônimos estáveis por caso (`<PESSOA_1>`…) com mapa persistido, worker dedicado (`anonymization`), semântica **fail-closed** (falha → `FAILED`, nunca LLM com texto real) e o **harness de benchmark** como critério de aceite técnico (recall por entidade, p95, contagens). A re-identificação na renderização (doctor/manager/admin) fica como serviço pronto para o presenter do change 07.

## What Changes

- **App `apps/anonymization`**: `deterministic.py` (regex: nº ocorrência, nome, nascimento, CPF/CNS), `recognizers.py` (BR_CPF/BR_CNS com checksum + BR_CRM), `engine.py` (AnalyzerEngine/AnonymizerEngine singleton com spaCy `pt_core_news_lg` + YAML de configuração), `services.py` (`anonymize_case_text` — merge determinístico×NLP, operador de pseudônimos estáveis), `tasks.py` (cluster `anonymization`), `reidentify.py`, signal de enqueue, comando `anonymization_benchmark`.
- **Campos novos no `Case`**: `anonymized_text`, `pseudonym_map` (JSON), `anonymization_report` (JSON), `patient_name`, `patient_birth_date` (consumidos por 06/07; linkage/prior-case).
- **Pseudônimos estáveis por caso**: valores distintos → tokens `<PESSOA_1>`, `<CPF_1>`, `<DATA_1>`, `<CRM_1>`, `<LOCAL_1>`, `<ORGANIZACAO_1>`, `<TELEFONE_1>`, `<EMAIL_1>`; mapa (token→valor) persistido no banco (PII no banco interno é aceito por decisão do dono; **nunca** vai ao LLM).
- **Worker `anonymization`** (django-q2 `ALT_CLUSTERS`): engine singleton por processo, workers 2/timeout 300; trigger por signal quando o caso entra em `ANONYMIZING` (emissão dos paths do change 04 — extração concluída, gate liberado); lock `worker_anonymization`; task idempotente por estado; `ANONYMIZING → LLM_EXTRACTING` via `complete_anonymization`; erro → `fail_processing` → `FAILED` (**fail-closed**). Compose: serviço `worker-anonymization`.
- **Re-identificação**: `reidentify_text(case, text)` substitui tokens pelos valores reais (controle de acesso fica com os consumers — presenter do 07 para doctor/manager/admin).
- **Benchmark**: comando com corpus JSONL (`text` + `expected[{value, entity_type}]`), relatório de recall por entidade/contagens/p50-p95 e varredura zero-PII (regex CPF/CNS/CheCksum) no output; exit ≠ 0 abaixo do mínimo (`ANONYMIZATION_BENCHMARK_MIN_RECALL`); corpus sintético versionado para testes.
- Dependências pinadas: `presidio-analyzer==2.2.364`, `presidio-anonymizer==2.2.364`, `spacy==3.8.*` + wheel travado de `pt_core_news_lg` (541 MB — instalado no build do worker, não em runtime).

## Capabilities

### `anonymization` (nova)

- Pré-extração determinística (linkage) e persistência de nome/nascimento.
- Anonimização com pseudônimos estáveis + mapa + relatório auditável.
- Fail-closed (nunca LLM sem anonimização bem-sucedida).
- Processamento assíncrono no cluster anonymization (trigger/idempotência/lock).
- Re-identificação controlada por caso.
- Benchmark como aceite técnico.

## Impact

- Arquivos: `apps/anonymization/**`, `apps/cases/models.py` (+5 campos) + migration, `apps/cases/events.py` (+`CASE_ANONYMIZATION_COMPLETED`), `config/settings/*` (cluster, thresholds, flags, YAML do NLP engine), `config/presidio-nlp-pt.yaml`, `docker-compose{,.dev}.yml` (+worker-anonymization), `pyproject.toml`/`uv.lock` (presidio/spacy/model), `.env.example`, `docs/adr/ADR-0007*`.
- FSM: usa transições existentes (`start_anonymization` self, `complete_anonymization`, `fail_processing`); **nenhum estado novo**.
- Ambiente: venv/imagens crescem ~700 MB (modelo spaCy); workers de anonimização não carregam o modelo no processo web.

## Non-goals

- Pipeline LLM/LLM1/LLM2 (06) — aqui só se estabelece o campo/invariante (`anonymized_text`) que o 06 consome.
- OCR/anonimização de anexos (10) — este change anonimiza apenas `Case.extracted_text`.
- Re-identificação em UI/presenter e seu controle de acesso (07).
- Recognizers adicionais (AIH/APAC/CEP/processo) e transformers/BERTimbau — benchmark futuro.
- Criptografia do mapa de pseudônimos (decisão registrada: banco interno; revisitar com DPO se exigido).
