# ADR-0007: Anonimização Presidio pt-BR embutida com fail-closed (design D10)

## Status

Accepted

## Contexto

O HMD precisa anonimizar o texto extraído do relatório SESAB (identificadores:
nome, nascimento, CPF, CNS, CRM, nº de ocorrência) antes de QUALQUER chamada
LLM — o texto real nunca sai do perímetro para o LLM (spec anonymization,
"Fail-closed antes de qualquer LLM"). A pesquisa web-verificada
(`temp/research/presidio-django-ptbr.md`) avaliou as opções: Presidio
2.2.364 (MIT, sob `data-privacy-stack`) com spaCy `pt_core_news_lg` 3.8.0
(~541 MB), sem recognizers nativos pt-BR com checksum, pseudônimos estáveis
por documento e política fail-closed; transformers (BERTimbau/BioBERTpt/GLiNER)
registrados como evolução pós-benchmark. Estado atual: extração + gate
(intake) levam o caso a `ANONYMIZING` com `extracted_text`; a FSM de casos
tem as transições `start_anonymization`/`complete_anonymization`/
`fail_processing` e locks de exclusividade por caso (change 03).

## Decisão

- **Bibliotecas Presidio embutidas em worker dedicado** (django-q2 cluster
  `anonymization`, processo `manage.py qcluster` com `Q_CLUSTER_NAME=
  anonymization`), não containers REST: o engine (`AnalyzerEngine` +
  `AnonymizerEngine`) é singleton por processo (`lru_cache`) e o modelo spaCy
  só é carregado nesse worker — o processo web nunca carrega o modelo.
  Dependências + modelo instalados no **build** da imagem (`uv sync --frozen
  --no-dev` no Dockerfile); os workers usam a imagem pronta (sem sync em
  runtime); o web dev mantém o fluxo de volume/runserver.
- **Trigger por signal desacoplado, com guarda anti-recursão** (D7): receiver
  de `CaseEvent.post_save` para `event_type == CASE_STATUS_ANONYMIZING` E
  `payload["source"] == "PDF_EXTRACTING"` (entrada real no estado); a
  self-transition `start_anonymization` da própria task emite `source=
  ANONYMIZING` e não re-dispara. Enqueue via `transaction.on_commit`
  (rollback não enfileira). Cobre os 3 paths de entrada (extração concluída,
  gate liberado, reenvio reprocessado) sem tocar o intake. Task
  `process_case_anonymization` idempotente por estado (só `ANONYMIZING`), sob
  lock do caso (`worker_anonymization`/`system`, release no `finally`).
- **Pseudônimos estáveis por caso** (`PseudonymOperator`, componente próprio —
  o operador custom do Presidio 2.2.364 foi reprovado por spike no slice 003):
  tokens `<PESSOA_N>`, `<CPF_N>`, `<DATA_N>`, etc. numerados por primeira
  ocorrência, consistentes no documento; mapa token → {valor real, tipo}
  persistido no `Case`; re-identificação (slice 005) usa o mapa 1:1.
- **Merge determinístico completo** (D6): pré-extração por regex dos
  identificadores canônicos + NER do Presidio; todas as ocorrências
  determinísticas localizadas com offsets válidos (mesmo sem NER) e
  sobreposições resolvidas por `(start asc, end desc, determinístico > NER)`.
- **Fail-closed**: falha em qualquer etapa da anonimização → a task converte em
  `fail_processing(motivo)` → `FAILED`, e `anonymized_text` permanece vazio
  (persistência + transição FSM num único `transaction.atomic()`); caso em
  `ANONYMIZING` sem `extracted_text` é retido pela task
  (`fail_processing("sem texto extraído")` — o núcleo é defensivo, quem valida
  é a task). Texto anonimizado não-vazio é pré-condição inabalável para
  qualquer chamada LLM.
- **Benchmark como critério de aceite técnico** (slice 005): harness com corpus
  (recall por tipo, latência p50/p95, varredura zero-PII, pico de RSS,
  documentos bloqueados; exit ≠ 0 abaixo do mínimo ou com vestígio).
- **Regras de operação**: `ANONYMIZATION_SCORE_THRESHOLD` (default 0.45);
  `ANONYMIZATION_SPACY_MODEL` (default `pt_core_news_lg`, trocável por `md` em
  ambientes enxutos); `ANONYMIZATION_RUN_TASKS_INLINE` (dev/teste `True` —
  determinístico; prod `False` — nunca inline sem intenção explícita);
  cluster `anonymization` no `Q_CLUSTER["ALT_CLUSTERS"]` (workers 2,
  timeout 300, retry 360 — separado do `pdf` para o modelo não competir com a
  extração).

## Alternativas Consideradas

1. **Containers REST do Presidio** (`presidio-analyzer`/`anonymizer` como
   serviços HTTP) — rejeitada (pesquisa §2): só valem com múltiplos consumers
   e isolamento de rede; aqui o consumer é único (worker) e o custo
   HTTP/auth/observabilidade não compensa; biblioteca embutida em worker
   persistente é a recomendação #1 da pesquisa.
2. **spaCy puro** (reconhecedor próprio sem Presidio) — rejeitada: perderia os
   recognizers pré-definidos e o pipeline analyzer/anonymizer consolidado do
   Presidio, aumentando a superfície de manutenção.
3. **Transformers** (BERTimbau/BioBERTpt/GLiNER) — rejeitada para o MVP:
   custo de infra/modelo maior sem recall comprovado em pt-BR clínico;
   registrada como evolução pós-benchmark.
4. **LLM local para anonimização** — rejeitada: anonimizar com o mesmo tipo de
   modelo que consumirá o texto anonimizado amplia a superfície de vazamento e
   é não-determinístico; o determinismo do merge é requisito de auditabilidade.

## Consequências

- **Imagem de worker maior** (~700 MB com o modelo spaCy `lg` no build) —
   aceite operacional; clusters separados (`pdf` × `anonymization`) evitam que
   o modelo dispute memória com a extração; workers 2 com singleton por
   processo e medição de RSS no benchmark (slice 005).
- **Recall clínico pt-BR não é garantido fora-da-caixa** (sem benchmark
   público pt-BR) — o merge determinístico cobre os identificadores canônicos
   do relatório SESAB; o benchmark local com corpus real é obrigatório antes de
   produção (aceite do plano §6.7); falso positivo é aceitável (recall >
   precisão antes do LLM). Caveats de datas: `DATE_TIME` → `<DATA_N>` (datas
   relativas não detectadas permanecem — timeline clínico relativo
   preservado); nascimento absoluto fica no `Case.patient_birth_date`.
- **Mapa de pseudônimos em claro no banco interno** — decisão do dono
   registrada neste ADR (sem criptografia em repouso no MVP); revisitável com o
   DPO. O mapa nunca sai do perímetro (nenhum prompt recebe o mapa).
- **Trigger altera o lifecycle de casos que já estavam em `ANONYMIZING`**: a
   partir deste change, entrada em `ANONYMIZING` dispara anonimização
   automática; caso sem texto falha fechado (tests do intake que cobrem o gate
   rodam com `ANONYMIZATION_RUN_TASKS_INLINE=False` — semântica de gate, não
   de pipeline).
- **Ambiente**: modelo instalado no `uv sync` (lock pinado por URL oficial do
   release) — primeira sincronização/build é lenta; troca por `pt_core_news_md`
   possível via `ANONYMIZATION_SPACY_MODEL` em ambientes enxutos.
- Positivo: pipeline determinístico e auditável (eventos + relatório de
  anonimização com contagens/modelo/versões por caso), zero dependência de
  serviço externo para a barreira de privacidade.
