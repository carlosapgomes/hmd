# Proposal: llm-pipeline-per-type

## Why

O caso chega a `LLM_EXTRACTING` com `anonymized_text` pronto (change 05), mas nada além do trabalho manual existe: é este change que transforma texto anonimizado em **estrutura clínica por tipo de procedimento** e em **recomendação consultiva** para o médico. É a generalização do pipeline v2 comprovado do ats-web (`orchestrator`/`llm1_service_v2`/`llm2_service_v2`/schemas Pydantic/reconciliação/policy/prior-case) para os 13 tipos do HMD, via OpenRouter (endpoint OpenAI-compatible), com as guardas anti-alucinação e o invariante de privacidade (LLM só vê tokens).

## What Changes

- **App `apps/llm`**: `PromptTemplate` versionado (unique `(name, version)`, 1 ativo por nome) + seeds idempotentes (`llm1.system`/`llm2.system` neutros + `proc.<type>.llm1.user`/`proc.<type>.llm2.user` × 13 tipos) + seleção/composição por tipos reconciliados.
- **App `apps/pipeline`**: cliente OpenRouter (SDK OpenAI, transport injetável p/ testes), schemas Pydantic v2 por tipo (base comum + blocos específicos + **composição união** p/ multiprocedimento — uma chamada LLM1 e uma LLM2 por caso), `llm1_service` (extração estruturada com guardas: strict JSON, language guard pt-BR, máx. 1 retry corretivo tipado, fail-closed), reconciliação declarado×detectado + **gate de divergência NIR**, `policy` determinística consultiva (thresholds S1–S8 do catálogo + requisitos gerais), `prior_case` (nº ocorrência 7d + fallback nome normalizado+nascimento 15d), `llm2_service` (sumário + sugestão por procedimento; **policy determinística vence o LLM**), `orchestrator`/`tasks` (cluster `llm`).
- **Campos novos no `Case`**: `structured_data`, `summary_text`, `suggested_action`, `policy_result` (JSON/Text; migrazão incremental).
- **FSM**: usa transições existentes + **uma nova** `bypass_pipeline_divergence` (`LLM_EXTRACTING → LLM_SUMMARIZING`) — primeira adição de transição pós-change-03, permitida pelo guardrail (transições sim, estados não). Divergência declarado×detectado → retenção com `manual_review_required` (campos existentes) + evento; NIR libera (extensão do gate_release do intake p/ escolher a transição conforme o estado de retenção).
- **Cluster `llm`** (django-q2 `ALT_CLUSTERS`, workers 1/timeout 900) + serviço `worker-llm` no compose; trigger por signal na entrada de `LLM_EXTRACTING` (padrão anti-recursão + `on_commit` do change 05); task idempotente por estado com retomada (`LLM_EXTRACTING` = pipeline completo; `LLM_SUMMARIZING` pós-bypass = só policy+prior+LLM2); lock `worker_llm`; falha em qualquer etapa → `FAILED` (fail-closed).
- **`manage.py llm_check`**: diagnóstico manual (conectividade/auth/modelos env; 1 chamada mínima por modelo) — fora do CI; benchmark de modelos flash (GLM-5.3-flash/DeepSeek-V4-Flash/Qwen3.8-Flash) é passo operacional pré-produção documentado, escolha via env.
- **ADR-0008** (pipeline per-type, composição união, policy determinística consultiva, fail-closed).

## Capabilities

### `llm-pipeline` (nova)

- Cliente OpenRouter (OpenAI-compatible) com erros tipados e diagnóstico manual.
- Extração estruturada por tipo (LLM1) com guardas anti-alucinação e fail-closed.
- Reconciliação declarado×detectado com gate de divergência NIR.
- Recomendação consultiva determinística (policy S1–S8 + requisitos gerais; nunca bloqueia).
- Prior-case (nº ocorrência 7d + fallback nome+nascimento 15d, por procedimento, auditável).
- Sumarização/apresentação (LLM2) com visão filtrada, policy vencendo o LLM.
- Processamento assíncrono no cluster llm (trigger/idempotência/lock/fail-closed).

## Impact

- Arquivos: `apps/llm/**`, `apps/pipeline/**` (schemas/policy/prior_case/services/orchestrator/tasks/tests), `apps/cases/{models,events}.py` (+4 campos, +eventos `CASE_PIPELINE_*`, `PRIOR_CASE_LOOKUP`, +transição), `apps/intake` (extensão mínima do gate_release p/ retenção em LLM_EXTRACTING), settings/compose/env, `pyproject.toml` (openai SDK), `docs/adr/ADR-0008*`.
- Consome: `anonymized_text` (única fonte LLM), `CaseProcedure`+`set_declared/detected_procedures`, `procedure_catalog` (tipos+thresholds+subtipos+anestésico), locks, signals.
- Custo operacional: chamadas OpenRouter por caso (2 LLM + retry eventual); volume 30–40/dia é leve.

## Non-goals

- Fila/presenter/decisão do médico (07), agendamento (08), resultado/encerramento/reenvio (09).
- OCR de anexos e `VISION_MODEL` (10).
- Dashboard/notificações (11); benchmark automatizado de modelos no CI (custo — `llm_check` + doc operacional).
- `priority_signals` (presente no plano §3 por herança do ats-web; **sem conceito correspondente no documento clínico HMD** — desvio registrado; se surgir, change futuro).
- Reprocessamento automático de caso `FAILED` (cria-se novo caso; recuperação é 09+).
