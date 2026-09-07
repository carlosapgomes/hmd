# llm-pipeline Specification (delta)

## Purpose

Transformar o texto anonimizado do relatório em estrutura clínica por tipo de procedimento e recomendação consultiva determinística, via OpenRouter, com guardas anti-alucinação, reconciliação com revisão NIR e fail-closed — o estágio do caso entre a anonimização e a fila médica.

## ADDED Requirements

### Requirement: Cliente OpenRouter com erros tipados

O sistema SHALL chamar LLMs via OpenRouter usando o endpoint OpenAI-compatible (SDK OpenAI), com chave/base/modelos por ambiente (`OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `LLM1_MODEL`, `LLM2_MODEL`) e timeout configurável. Falhas de rede/autenticação/limite SHALL ser erros tipados internamente. Um comando de diagnóstico manual SHALL validar conectividade/autenticação com uma chamada mínima por modelo configurado (fora do CI; sem custo relevante).

#### Scenario: Diagnóstico manual valida os modelos configurados

- **GIVEN** as variáveis de ambiente do OpenRouter configuradas
- **WHEN** o comando de diagnóstico é executado
- **THEN** ele reporta, por modelo (`LLM1_MODEL`, `LLM2_MODEL`), sucesso ou erro tipado (auth/rede/outro) sem depender do banco de casos

#### Scenario: Erro de autenticação é tipado

- **GIVEN** uma credencial inválida configurada
- **WHEN** uma chamada é feita pelo cliente
- **THEN** o erro interno carrega o tipo `auth` (e não um texto genérico), permitindo tratamento e log diferenciados

### Requirement: Extração estruturada por tipo com guardas anti-alucinação

A LLM1 SHALL extrair, a partir **exclusivamente** de `anonymized_text`, um artefato estruturado validado por schemas por tipo de procedimento: base comum (identificação mínima do pedido com tokens, contexto clínico, linha do tempo, exames com resultados objetivos, medicações/anticoagulação, comorbidades, contraindicações, `trechos_nao_classificados`) + blocos específicos por tipo (ex.: FAV → estado do acesso; filtro cava → TEp/contraindicação a anticoagulação; permicath → infecção ativa). Casos multiprocedimento usam **composição união** dos schemas numa **única chamada**; a lista de procedimentos detectados aceita qualquer tipo do catálogo (não apenas os declarados) — o modelo pode relatar um tipo não-declarado, que é o gatilho da revisão de divergência. Campos extraídos SHALL carregar `evidence_spans` (trecho citado do texto) e `status ∈ {confirmado, nao_informado, incerto}`; informação ausente NUNCA é completada. Guardas: validação strict do JSON contra o schema, language guard pt-BR (rejeita resposta em outro idioma), **no máximo 1 retry corretivo tipado** por chamada; esgotadas as guardas, o caso vai a `FAILED` com motivo (fail-closed) — nunca avança com artefato inválido.

#### Scenario: Extração feliz valida e persiste

- **GIVEN** um caso em processamento com texto anonimizado contendo o relatório padrão e um tipo declarado
- **WHEN** a LLM1 executa com resposta válida
- **THEN** o artefato validado é persistido no caso (apenas tokens — sem valores reais) e a trilha registra o evento com os nomes/versões dos prompts e os tipos declarados

#### Scenario: Resposta inválida esgota retry e falha fechado

- **GIVEN** uma resposta que viola o schema e um retry corretivo que também viola
- **WHEN** as guardas terminam
- **THEN** o caso transita para `FAILED` com motivo na trilha e nenhum artefato inválido é persistido

#### Scenario: Resposta em idioma errado é rejeitada

- **GIVEN** uma resposta válida em schema mas em inglês
- **WHEN** o language guard roda
- **THEN** a resposta é rejeitada e o fluxo de retry corretivo é acionado

### Requirement: Reconciliação declarado×detectado com gate de divergência

O pipeline SHALL reconciliar os procedimentos declarados pelo NIR com os detectados pela extração (atualizando o status de detecção por row via serviço existente). Havendo divergência (tipo detectado não declarado, ou declarado sem detecção), o caso SHALL ficar **retido** para revisão do NIR: permanece em `LLM_EXTRACTING` com flag de revisão e motivo, com evento auditável — e **nunca** vai à sumarização/fila médica sem resolução. O NIR pode liberar a divergência (bypass com evento), o que avança o caso com o conjunto declarado mantido.

#### Scenario: Declaração e detecção coincidem

- **GIVEN** um caso com `art_perif` declarado e detectado na extração
- **WHEN** a reconciliação executa
- **THEN** a row do procedimento fica `detected` e o pipeline prossegue sem retenção

#### Scenario: Divergência retém o caso para o NIR

- **GIVEN** um caso com `art_perif` declarado e `nefrostomia` detectado (não declarado)
- **WHEN** a reconciliação executa
- **THEN** o caso permanece em `LLM_EXTRACTING` com flag de revisão e motivo de divergência na trilha, sem avançar

#### Scenario: Liberação da divergência avança com declarado

- **GIVEN** um caso retido por divergência
- **WHEN** o NIR libera
- **THEN** o caso avança para a sumarização com evento de bypass, mantendo o conjunto declarado

### Requirement: Recomendação consultiva determinística por procedimento

O sistema SHALL avaliar, para cada procedimento do caso, critérios clínicos determinísticos a partir do artefato extraído: thresholds da seção do tipo (S1–S8 do catálogo) e requisitos gerais (anticoagulantes/antiagregantes com protocolo de suspensão, metformina, alergia a contraste, peso > 180 kg, jejum, isolamento, creatinina ≥ 1,5 com nefroproteção/liberação de Nefrologia, suporte anestésico). Cada critério produz `ok | alerta (com motivo) | nao_informado`; o resultado global por procedimento é `recomenda_aceitar` ou `recomenda_recusar` com a lista de motivos. A policy **nunca bloqueia** — recomenda e explica; o médico decide. O resultado SHALL ser persistido no caso e resumido em evento.

#### Scenario: Critério fora do threshold gera alerta

- **GIVEN** um procedimento `cat_cardiaco` com plaquetas extraídas abaixo de 100.000 (status confirmado)
- **WHEN** a policy avalia
- **THEN** o critério de plaquetas resulta em `alerta` com motivo e a recomendação global do procedimento lista esse motivo

#### Scenario: Informação ausente é sinalizada, não inventada

- **GIVEN** um procedimento sem INR extraído
- **WHEN** a policy avalia
- **THEN** o critério INR resulta em `nao_informado` (distinto de ok/alerta) e a recomendação registra a ausência

#### Scenario: Todos os critérios ok recomenda aceitar

- **GIVEN** um procedimento com todos os critérios dentro dos thresholds e requisitos gerais ok
- **WHEN** a policy avalia
- **THEN** a recomendação global é `recomenda_aceitar` sem motivos de recusa

### Requirement: Prior-case com fallback por nome e nascimento

O sistema SHALL localizar casos prévios do mesmo paciente para informar o médico e o LLM2: chave primária pelo número de ocorrência (janela configurável, default 7 dias) e **fallback** por nome normalizado + data de nascimento idênticos, quando o caso atual foi criado até 15 dias após a decisão do prévio (janela configurável). O lookup é por procedimento, exclui o próprio caso e casos sem decisão, e produz um resumo (data, desfecho, motivo). O evento de lookup SHALL registrar a origem do match (`occurrence_number | name_birthdate_fallback`).

#### Scenario: Match por número de ocorrência dentro da janela

- **GIVEN** um caso prévio decidido há 3 dias com o mesmo número de ocorrência
- **WHEN** o lookup executa para um procedimento em comum
- **THEN** o resumo do prévio é retornado com origem `occurrence_number` e o evento auditável é gravado

#### Scenario: Fallback por nome+nascimento dentro de 15 dias

- **GIVEN** um caso prévio decidido há 10 dias com número de ocorrência diferente, mesmo nome normalizado e nascimento
- **WHEN** o lookup executa
- **THEN** o resumo é retornado com origem `name_birthdate_fallback` e evento correspondente

#### Scenario: Sem prévio fora das janelas

- **GIVEN** casos prévios do mesmo paciente com decisão há mais de 15 dias
- **WHEN** o lookup executa
- **THEN** nenhum prévio é retornado

### Requirement: Sumarização com policy determinística prevalecendo

A LLM2 SHALL produzir, numa única chamada por caso, o sumário apresentável e a sugestão por procedimento, recebendo **apenas** uma cópia do artefato LLM1 filtrada pelos tipos reconciliados, o resultado da policy e os resumos de prior-case — tudo em tokens. A sugestão final SHALL respeitar a decisão da policy determinística: se a policy recomenda recusar um procedimento, a sugestão do LLM para esse procedimento É recusa (o LLM não pode "suavizar"); a recomendação mais restritiva prevalece no agregado do caso. Falhas seguem o mesmo regime fail-closed da extração.

#### Scenario: Policy de recusa prevalece sobre sugestão do LLM

- **GIVEN** um procedimento cuja policy recomenda recusar e uma resposta LLM2 sugerindo aceitar
- **WHEN** a reconciliação final aplica a precedência
- **THEN** a sugestão persistida para o procedimento é recusa, com os motivos da policy

#### Scenario: Sumário alimentado apenas por tokens

- **GIVEN** a chamada LLM2 de um caso
- **WHEN** o payload é montado
- **THEN** ele contém exclusivamente conteúdo anonimizado (varredura recursiva contra os mapas de pseudônimos do caso e dos casos prévios — nenhum valor real de paciente)

### Requirement: Processamento assíncrono no cluster llm

Ao entrar em `LLM_EXTRACTING`, o caso SHALL ter o pipeline enfileirado no cluster `llm` (worker dedicado). A task SHALL ser idempotente por estado (`LLM_EXTRACTING` = pipeline completo; `LLM_SUMMARIZING` pós-bypass = retoma da policy/sumarização), operar sob lock do caso, registrar as etapas na trilha e levar o caso a `AWAITING_DOCTOR` apenas ao concluir extração+reconciliação+policy+prior-case+sumarização sem retenções. Falha em qualquer etapa → `FAILED` com motivo (fail-closed).

#### Scenario: Pipeline completo leva à fila médica

- **GIVEN** um caso que entra em `LLM_EXTRACTING` com texto anonimizado e sem divergência
- **WHEN** a task executa
- **THEN** o caso atravessa `LLM_SUMMARIZING` e chega a `AWAITING_DOCTOR` com artefatos persistidos e eventos das etapas

#### Scenario: Retomada pós-bypass não refaz a extração

- **GIVEN** um caso em `LLM_SUMMARIZING` após liberação de divergência
- **WHEN** a task executa
- **THEN** apenas policy/prior-case/sumarização rodam (a extração persistida é reaproveitada) e o caso chega a `AWAITING_DOCTOR`

#### Scenario: Reexecução é no-op

- **GIVEN** um caso já em `AWAITING_DOCTOR`
- **WHEN** a task executa novamente
- **THEN** nada muda e nenhum evento é gravado
