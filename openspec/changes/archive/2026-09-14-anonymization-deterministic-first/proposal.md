# Proposal: anonymization-deterministic-first

## Why

Caso real da fase 2 (3ca56d86, art_perif) falhou em `llm1_token_leak` de forma
sistêmica: o NER (spaCy pt_core_news_lg via Presidio) classificou vocabulário
clínico como PESSOA e LOCAL (mapa com "Afebril", "DORSO", "PAS:100\nPAD:80",
"FC:77\nFR:18", "Mot", "Solicit", "Prof", "Reg", "Macro", "Murmúrio"…), e
esses termos aparecem como substring em qualquer artefato de LLM → guard
fail-closed em TODO caso real. Nenhum PHI vazou. Análise com o dono
(2026-09-14): a camada que protege o paciente é a **extração determinística**
(rótulos SESAB + runs de dígitos, ~100% precisa, varre TODAS as ocorrências
dos valores); o NER existe só para terceiros, com precisão comprovada ruim
no domínio clínico — e a referência ats-web nem anonimiza (manda
`extracted_text` cru ao LLM).

**Decisão do dono (política clínica)**: fase 2 com anonimização
**determinística-only** — identidade do paciente ponta a ponta tokenizada;
nomes de TERCEIROS citados no texto corrido seguem em claro ao OpenRouter
(postura igual à do ats-web em produção; risco aceito conscientemente).
Guard de retorno INTOCADO (com mapa limpo ele se torna confiável).

## What Changes

- Setting `ANONYMIZATION_USE_NER` (default **False** — camada ruidosa é
  opt-in): quando False, `anonymize_text` NÃO constrói o engine Presidio nem
  consulta o analyzer (workers economizam o carregamento do spaCy lg);
  restam os candidatos determinísticos (merge/operação inalterados).
- `config/settings/test.py` pina `ANONYMIZATION_USE_NER=True` (suíte segue
  exercitando o caminho NER via stubs existentes; novos testes cobrem o
  caminho OFF com `override_settings(False)`).
- Benchmark (`anonymization_benchmark`) segue calibrando a camada NER:
  comando ganha a semântica documentada de rodar com o setting, e o teste de
  CI que valida o corpus sintético (nome adversarial) roda com
  `override_settings(ANONYMIZATION_USE_NER=True)`.
- `prior_case._anonymized_reason` passa a semear com o `pseudonym_map` do
  caso anterior (fecha o residual de nome de paciente em motivos de negatura
  sem rótulo — determinístico, sem NER).
- Pino E2E do incidente: texto sintético com os fragmentos reais (vocabulário
  clínico genérico) → mapa SÓ determinístico; artefato representativo
  contendo "profissional"/"Registro"/"Macro" passa no `_assert_tokens_only`.
- Docs: `.env.example`/compose sem novas envs obrigatórias (default já é o
  novo posture); README documenta a decisão, o trade-off de terceiros e como
  reativar o NER (`ANONYMIZATION_USE_NER=true` + benchmark p/ calibrar).

## Capabilities

### Modified: `anonymization`

- ADDED requirement "Anonimização determinística-first (fase 2)".

## Impact

- Sem migration; guard inalterado; workers continuam iguais (worker-
  anonymization deixa de carregar spaCy quando NER off — mem real cai,
  `WORKER_ANONYMIZATION_MEM_LIMIT` segue tunável).
- O change anteriormente planejado (`anonymization-pessoa-precision`,
  portões de forma/stoplist) é SUBSTITUÍDO por este (decisão do dono);
  aprendizados do incidente ficam registrados no design.

## Não-goais

- Não remove o código NER/Presidio/benchmark/recognizers (reversível por
  env); não mexe no guard `_assert_tokens_only`; não altera extração
  determinística; não trata terceiros (trade-off aceito).
