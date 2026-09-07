# ADR-0008: Pipeline LLM per-type com composição união e guards fail-closed

## Status

Accepted

## Contexto

O pipeline HMD precisa transformar o texto **anonimizado** do relatório
(change 05) em: extração estruturada com evidência (LLM1), recomendação
determinística consultiva por procedimento (policy), contexto de casos
prévios (prior-case) e, ao fim, um sumário apresentável com sugestão por
procedimento (LLM2) que alimenta a fila médica (change 07). São 13 tipos de
procedimento (catálogo code-first, change 03) com seções de critérios S1–S8.
Riscos a mitigar: alucinação/omissão do LLM, custo/latência de chamadas
externas (OpenRouter), vazamento de PII (invariante do change 05: o LLM só vê
tokens), divergência entre o que o NIR declarou e o que o modelo detectou, e
casos retidos/falhos que não podem avançar para a decisão médica. O caso
chega a `LLM_EXTRACTING` com `anonymized_text` + `pseudonym_map` (change 05);
a FSM tem as transições `start_llm_extraction`/`complete_llm_extraction`/
`start_llm_summarization`/`complete_llm_summarization`/`fail_processing`/
`bypass_pipeline_divergence` (change 03 + slice 004); locks de exclusividade
por caso e o padrão signal+task+cluster do django-q2 vieram nos changes 03–05.

## Decisão

- **Análise per-type com composição união em UMA chamada por estágio**
  (lição do ADR-0004 do ats-web): schema LLM1 = base comum + blocos específicos
  apenas dos tipos declarados; detecção livre sobre o catálogo completo
  (qualquer um dos 13 tipos pode ser reportado em
  `pedido.procedimentos_solicitados` — sem isso o gate de divergência nunca
  dispararia). O schema LLM2 cobre sumário + um item de sugestão por
  procedimento na mesma chamada. Custo: 2 chamadas/caso (+ retry eventual).
- **Guardas anti-alucinação/omissão** (LLM1 e LLM2): schema estrito pydantic
  (`extra="forbid"`, `response_format` JSON schema strict com `required`
  completo e `additionalProperties: false`), language guard pt-BR por lista
  canônica de marcadores, retry corretivo **único** tipado (schema/idioma —
  a resposta inválida anterior nunca é reenviada), assert de sanidade
  **recursivo** de tokens (nenhum valor real dos mapas de pseudônimos do caso
  e dos casos prévios no payload/prompt e na resposta validada), proveniência
  obrigatória (`evidence_spans` + status `confirmado|nao_informado|incerto`).
- **Reconciliação + gate de divergência**: a detecção é reconciliada com o
  declarado (`match | missing_declaration | not_detected`); divergência retém
  o caso em `LLM_EXTRACTING` (`procedure_divergence` + evento) — nunca
  sumariza/fila sem resolução; a liberação do NIR usa a transição
  `bypass_pipeline_divergence` (→ `LLM_SUMMARIZING`, conjunto **declarado**
  prevalece) e a retomada **não refaz a extração**.
- **Policy determinística consultiva prevalecendo**: thresholds de seção do
  catálogo (S1–S8) + requisitos gerais (anticoagulantes por fármaco com
  protocolo, antiagregantes, metformina, alergia, peso > 180 kg, jejum,
  isolamento, Cr ≥ 1,5 com nefroproteção, suporte anestésico) — nunca
  bloqueia, recomenda e explica. Na reconciliação final do LLM2, policy com
  `recomenda_recusar` ⇒ sugestão do procedimento é **recusa com os motivos da
  policy** (o LLM não suaviza); o **agregado do caso** é consultivo: qualquer
  procedimento com sugestão final recusar ⇒ agregado recusa com os motivos
  somados. O `strictest_global_support` do ats-web referia-se apenas ao
  suporte anestésico e **não** foi reutilizado.
- **Prior-case com fallback**: por procedimento, chave primária por nº de
  ocorrência (janela 7 dias) e fallback por nome normalizado + nascimento
  (janela 15 dias, intervalo fechado); exclui o próprio caso e casos sem
  decisão; o motivo (texto livre do médico) entra no LLM2 **pré-anonimizado**
  pelo núcleo do change 05 (à UI do médico vai o original, change 07); origem
  do match auditada em evento.
- **Orquestrador único dono do fail-closed**: `process_case_pipeline`
  idempotente por estado (`LLM_EXTRACTING` = pipeline completo;
  `LLM_SUMMARIZING` = retomada pós-bypass; demais estados → no-op), sob lock
  do caso (`worker_llm`/`system`); **serviços nunca transicionam** — só o
  orquestrador chama `fail_processing(tipo)` → `FAILED`. Trigger por signal
  com guarda anti-recursão (`payload["source"]` + `actor_type`): entrada real
  em `LLM_EXTRACTING` (`source == ANONYMIZING`) e retomada pós-bypass
  (`LLM_SUMMARIZING` com `source == LLM_EXTRACTING` E ator **user**) enfileiram
  no cluster `llm` via `transaction.on_commit`; o avanço natural do
  orquestrador tem ator **system** e não re-enfileira. Coordenação
  **transição de saída + release da lease no mesmo atomic** quando o evento é
  consumido por signal (padrão fixado no change 05, estendido no slice 006 à
  saída da anonimização — entrada do pipeline — e à saída da sumarização).
- **Modelos por env com benchmark operacional**: `LLM1_MODEL`/`LLM2_MODEL`
  por ambiente; a escolha do modelo é decisão operacional via `manage.py
  llm_check` (chamada mínima por modelo + reporte de erro tipado) — sem
  benchmark no CI (custo).
- **Regras de operação**: cluster `llm` no `Q_CLUSTER["ALT_CLUSTERS"]`
  (workers 1, timeout 900, retry 960 — separado dos demais para as chamadas
  externas não competirem com extração/anonimização); `LLM_RUN_TASKS_INLINE`
  (dev/teste `True` — determinístico; prod `False` — nunca inline sem intenção
  explícita; o processo web nunca chama a OpenRouter); eventos enxutos por
  etapa (prompts/versões, contagens, classificações — nunca conteúdo clínico
  bruto); prompts versionados com 1-ativo por nome (slice 003).

## Alternativas Consideradas

1. **N chamadas por tipo** (uma chamada LLM por procedimento em cada estágio)
   — rejeitada: custo multiplicado e contexto clínico duplicado; a lição do
   ADR-0004 do ats-web é a composição união em chamada única.
2. **Policy dentro do prompt do LLM** (deixar o modelo avaliar os critérios)
   — rejeitada: a avaliação clínica determinística deve ser auditável e
   reprodutível (mesma entrada → mesma saída); o LLM apenas consome o
   resultado e não pode suavizá-lo.
3. **Sem gate de divergência** (sumarizar direto com o que o LLM detectou) —
   rejeitada: o NIR declarou o pedido; procedimento detectado sem declaração
   (ou declarado não-detectado) precisa de resolução humana auditável antes de
   qualquer recomendação/fila.
4. **Reuso do `strictest_global_support` do ats-web** — rejeitada: no HMD o
   agregado do caso é definido explicitamente como "qualquer procedimento
   recusado ⇒ recusa com motivos somados"; o strictest do ats-web aplicava-se
   apenas à recomendação de suporte anestésico, conceito fora do escopo HMD.

## Consequências

- **Custo/latência**: 2 chamadas/caso à OpenRouter (+ retry corretivo
  eventual); volume leve do HMD; timeout e modelos por env; escolha de modelo
  por benchmark operacional, não fixada no código.
- **Divergência depende de calibração**: a retenção por divergência pode ser
  frequente enquanto o LLM1 não for calibrado com o corpus real de relatórios;
  a reconciliação tolerante a rótulos e o corpus real (benchmark operacional)
  são o caminho de ajuste.
- **LLM2 é refém da qualidade da LLM1**: a visão estruturada do LLM2 vem do
  artefato LLM1 (só tokens); a policy e os prior-cases mitigam, mas omissões da
  extração limitam o sumário — os gates de evidência/status tri-state
  sinalizam ausência em vez de inventá-la.
- **Invariante do change 05 preservada**: nenhum prompt/payload do pipeline
  recebe valores reais (assert recursivo por caso e casos prévios); o mapa de
  pseudônimos nunca sai do perímetro.
- **Cadeia inline single-process exige coordenação de locks**: a saída da
  anonimização (entrada do pipeline) e a saída da sumarização (entrada da
  fila médica, 07) liberam a lease no mesmo atomic da transição cujo evento é
  consumido por signal — sem isso, dev/teste inline conflitaria e prod teria
  uma janela de race com a task dropada e caso preso.
- Positivo: trilha completa e auditável por caso (eventos das etapas com
  prompts/versões), fail-closed honesto (nenhum caminho leva texto real ou
  artefato inválido adiante), e o médico decide com sugestão consultiva
  respaldada pela policy determinística.
