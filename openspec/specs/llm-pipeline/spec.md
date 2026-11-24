# llm-pipeline Specification

## Purpose

Transformar o texto anonimizado do relatório em estrutura clínica por tipo de procedimento e recomendação consultiva determinística, via OpenRouter, com guardas anti-alucinação, reconciliação com revisão NIR e fail-closed — o estágio do caso entre a anonimização e a fila médica.

## Requirements

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

O pipeline SHALL reconciliar os procedimentos declarados pelo NIR com os detectados pela extração, atualizando a detecção real por procedimento. Havendo divergência sem confirmação humana válida, mas com fonte e anonimização íntegras, o caso SHALL permanecer retido em `LLM_EXTRACTING`, com flag, motivo e evento auditável, sem sumarização nem fila médica. Inconsistência de fonte/anonimização no reprocessamento SHALL resultar em FAILED operacional, não nesse fallback de reconciliação. Nos novos envios, a confirmação explícita de um procedimento, mantido ou trocado, SHALL definir o objeto da análise no reprocessamento e resolver a divergência operacional sem modificar a evidência automática. Casos históricos sem o novo contrato mantêm a liberação por bypass com conjunto declarado preservado.

#### Scenario: Declaração e detecção coincidem
- **GIVEN** um caso com `art_perif` declarado e detectado na extração
- **WHEN** a reconciliação executa
- **THEN** a row do procedimento fica `detected` e o pipeline prossegue sem retenção

#### Scenario: Divergência retém o caso para o NIR
- **GIVEN** um caso com `art_perif` declarado e `nefrostomia` detectado (não declarado), sem confirmação válida
- **WHEN** a reconciliação executa
- **THEN** o caso permanece em `LLM_EXTRACTING` com flag de revisão e motivo de divergência na trilha, sem avançar

#### Scenario: Liberação da divergência avança com declarado
- **GIVEN** caso histórico sem o novo contrato e retido por divergência
- **WHEN** o NIR libera pelo contrato anterior
- **THEN** o caso avança para a sumarização com evento de bypass, mantendo o conjunto declarado, sem registrar confirmação humana nova

#### Scenario: Mesma detecção divergente não repete a revisão após confirmação
- **GIVEN** caso novo em que o NIR confirmou A e a fonte/revisão não mudaram
- **WHEN** a nova extração volta a detectar B ou nenhum procedimento
- **THEN** A dirige a análise, a diferença automática permanece auditada e nenhuma nova retenção é criada apenas por essa divergência

#### Scenario: Confirmação inválida não dispensa o gate
- **GIVEN** confirmação cuja revisão ou declaração não corresponde mais ao caso, mantendo fonte, texto anonimizado e mapa íntegros
- **WHEN** o pipeline reconcilia
- **THEN** a confirmação é invalidada de forma auditável e, havendo divergência, a revisão NIR volta a ser exigida em vez de aplicar autorização antiga; sem divergência, a análise automática pode prosseguir
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

O sistema SHALL localizar contexto de decisões anteriores por procedimento usando a identidade de `case-reappearance`: nome normalizado e regulação ou CNS válido iguais, ou confirmação NIR vigente de candidato fraco/conflitante. O lookup SHALL excluir o próprio caso e decisões futuras, usar janela única configurável de 15 dias após a decisão e produzir data, desfecho, motivo anonimizado e contagem de negativas. Origens SHALL ser `occurrence_number`, `patient_cns`, `nir_confirmed` ou `none` no evento sem match. Nascimento deixa de ser necessário no modo novo. Feature desligada SHALL preservar lookup legado de regulação 7 dias e nome+nascimento 15 dias, com suas settings depreciadas; feature ligada usa só a janela nova.

#### Scenario: Match por número de ocorrência dentro da janela
- **GIVEN** caso prévio decidido há 3 dias com nome e número de ocorrência iguais
- **WHEN** lookup executa para o mesmo procedimento
- **THEN** resumo do prévio é retornado com origem `occurrence_number` e evento auditável

#### Scenario: Fallback por nome+nascimento dentro de 15 dias
- **GIVEN** caso prévio decidido há 10 dias com regulação diferente, nome e CNS válidos iguais, sem nascimento
- **WHEN** lookup executa
- **THEN** resumo é retornado com origem `patient_cns` e evento correspondente, sem depender de nascimento

#### Scenario: Sem prévio fora das janelas
- **GIVEN** decisões médicas registradas há mais de 15 dias em relação à criação do novo
- **WHEN** lookup executa
- **THEN** nenhum prévio fora da janela é retornado, mesmo por regulação

#### Scenario: Limites fechados e decisão futura
- **WHEN** decisões ocorreram exatamente na criação, exatamente 15 dias antes ou depois da criação do novo
- **THEN** os dois primeiros limites são elegíveis e a decisão futura é excluída

#### Scenario: Suspeita fraca não vira contexto factual
- **WHEN** só nome+origem coincide e o NIR ainda não respondeu ou marcou independente
- **THEN** caso suspeito não alimenta o contexto do LLM2 nem o card como prévio confirmado

#### Scenario: Negativa encerrada permanece consultiva
- **WHEN** caso CLEANED tem negativa dentro da janela e identidade válida para o novo pedido
- **THEN** decisão anterior e motivo informam o novo ciclo sem reaplicar negativa automaticamente, com motivo anonimizado antes de envio externo

#### Scenario: Confirmação usa código canônico
- **WHEN** candidato fraco ou conflitante é confirmado pelo NIR em revisão vigente
- **THEN** contexto tem origem `nir_confirmed`, com confirmação correlacionada e sem identificadores no evento

#### Scenario: Rollout desligado preserva lookup legado
- **WHEN** feature está desligada e há decisão por regulação há 10 dias sem nascimento
- **THEN** janela legada de 7 dias não retorna match; habilitando feature, mesmo nome e regulação podem casar na janela única de 15 dias

#### Scenario: Contexto anterior coexiste com fila ativa
- **WHEN** existe negativa histórica elegível e candidato ativo válido para o novo
- **THEN** negativa pode informar o contexto e resolução de fila ocorre independentemente, sem consumir exclusividade de vínculo operacional

#### Scenario: Confirmação posterior à sumarização
- **WHEN** NIR confirma vínculo com negativa depois da sumarização do novo
- **THEN** card médico mostra o contexto confirmado e distingue sua inclusão posterior, sem afirmar que o LLM2 já o considerou nem alterar decisão médica registrada

### Requirement: Sumarização com policy determinística prevalecendo

A LLM2 SHALL produzir, numa única chamada por caso, o sumário apresentável e a sugestão por procedimento, recebendo **apenas** uma cópia do artefato LLM1 filtrada pelos tipos reconciliados ou confirmados, o resultado da policy, os resumos de prior-case e, quando aplicável, contexto técnico da confirmação humana — tudo anonimizado. A sugestão final SHALL respeitar a decisão da policy determinística: se a policy recomenda recusar um procedimento, a sugestão do LLM para esse procedimento É recusa (o LLM não pode "suavizar"); a recomendação mais restritiva prevalece no agregado do caso. Confirmação humana não constitui evidência clínica, não autoriza inventar dados e não altera a policy. Falhas seguem o mesmo regime fail-closed da extração.

#### Scenario: Policy de recusa prevalece sobre sugestão do LLM
- **GIVEN** um procedimento cuja policy recomenda recusar e uma resposta LLM2 sugerindo aceitar
- **WHEN** a reconciliação final aplica a precedência
- **THEN** a sugestão persistida para o procedimento é recusa, com os motivos da policy

#### Scenario: Sumário alimentado apenas por tokens
- **GIVEN** a chamada LLM2 de um caso
- **WHEN** o payload é montado
- **THEN** ele contém exclusivamente conteúdo anonimizado (varredura recursiva contra os mapas de pseudônimos do caso e dos casos prévios — nenhum valor real de paciente)

#### Scenario: Confirmação sem detecção específica ainda recebe análise
- **GIVEN** A confirmado e nova extração sem A na lista detectada
- **WHEN** a sumarização é executada
- **THEN** produz resumo e sugestão exatamente para A, usando dados comuns e ausências explícitas, sem inserir A ou evidências inventadas no artefato automático

#### Scenario: Texto livre da justificativa não é enviado ao LLM
- **GIVEN** confirmação com justificativa contendo dados identificadores ou instruções livres
- **WHEN** as chamadas LLM1 e LLM2 são montadas
- **THEN** a justificativa e identidade do autor não integram o payload externo; o contexto de confirmação é técnico e não fornece nova evidência clínica

#### Scenario: Confirmação não suaviza recusa da policy
- **GIVEN** procedimento confirmado com policy recomendando recusar
- **WHEN** LLM2 sugere aceitar
- **THEN** a sugestão persistida é recusa com os motivos da policy, mantendo a decisão final do médico livre
### Requirement: Processamento assíncrono no cluster llm

Ao entrar em `LLM_EXTRACTING`, o caso SHALL ter o pipeline enfileirado no cluster `llm`. Nos novos envios, pedido de reprocessamento após confirmação SHALL executar novamente extração, reconciliação, policy, prior-case e sumarização, sob exclusividade de mutação e após commit, sem reextrair PDF. A task SHALL registrar etapas e ser idempotente quanto aos efeitos concluídos; retomada histórica em `LLM_SUMMARIZING` continua sem refazer LLM1. O caso SHALL chegar a `AWAITING_DOCTOR` apenas com extração válida, resumo não vazio e análise completa do conjunto efetivo, sem retenção pendente. Falha em etapa leva a `FAILED` com motivo auditável, nunca a encaminhamento do relatório puro.

#### Scenario: Pipeline completo leva à fila médica
- **GIVEN** um caso que entra em `LLM_EXTRACTING` com texto anonimizado e sem divergência
- **WHEN** a task executa
- **THEN** o caso atravessa `LLM_SUMMARIZING` e chega a `AWAITING_DOCTOR` com artefatos persistidos e eventos das etapas

#### Scenario: Retomada pós-bypass não refaz a extração
- **GIVEN** caso histórico em `LLM_SUMMARIZING` após liberação de divergência
- **WHEN** a task executa
- **THEN** apenas policy/prior-case/sumarização rodam, reaproveitando a extração persistida, e o caso chega a `AWAITING_DOCTOR`

#### Scenario: Reexecução é no-op
- **GIVEN** um caso já em `AWAITING_DOCTOR`
- **WHEN** a task executa novamente
- **THEN** nada muda e nenhum evento é gravado

#### Scenario: Confirmação refaz LLM1 mesmo mantendo o exame
- **GIVEN** caso novo cuja declaração A foi ratificada pelo NIR
- **WHEN** o pedido de reprocessamento é executado
- **THEN** há nova chamada LLM1 com perfil A, nova policy/prior-case e LLM2 para A, sem chamar extrator PDF ou reutilizar resultados derivados anteriores

#### Scenario: Troca usa perfil e seção clínica do confirmado
- **GIVEN** caso inicialmente declarado A e confirmado como C
- **WHEN** o pipeline reprocessa
- **THEN** extração, critérios da seção, prior-case e sugestão correspondem a C, sem conservar análise residual de A

#### Scenario: Ausência de artefatos impede encaminhamento
- **GIVEN** caso novo sem extração válida, resumo vazio ou só de espaços, policy incompleta ou sugestões que não cobrem exatamente o confirmado
- **WHEN** tenta concluir o processamento
- **THEN** não emite chegada à fila médica, não aceita resultados antigos como substitutos e registra a falha

#### Scenario: Erro de LLM nunca encaminha relatório puro
- **GIVEN** caso confirmado em reprocessamento
- **WHEN** uma chamada falha ou esgota suas guardas
- **THEN** o caso fica `FAILED`, visível como falha, sem fila médica nem fallback de aprovação/encaminhamento sem análise

#### Scenario: Encerramento durante processamento não ressuscita caso
- **GIVEN** chamada LLM em voo
- **WHEN** o caso é encerrado administrativamente antes da resposta, inclusive com dados minimizados
- **THEN** o estado encerrado é verificado antes de qualquer invalidação/falha: a resposta não restaura dados, não encaminha e não grava eventos de confirmação invalidada ou FAILED por causa dos campos removidos

### Requirement: Integridade de fonte e anonimização no reprocessamento

O worker SHALL verificar fonte, texto anonimizado e mapa contra o snapshot antes de envio ou persistência. Inconsistência ou ausência SHALL invalidar a confirmação e causar FAILED operacional sem novo dispatch ou encaminhamento. Revisão/declaração alterada só permite fallback com integridade preservada. Caso já encerrado SHALL abortar antes de invalidar ou falhar.

#### Scenario: Fonte alterada impede dispatch mesmo sem divergência clínica
- **GIVEN** confirmação persistida e fonte principal alterada ou indisponível
- **WHEN** o worker tenta reprocessar, mesmo que a detecção anterior coincida com o declarado
- **THEN** registra invalidação sem duplicação equivalente, permanece fora da fila médica e termina FAILED operacional sem nova chamada externa nem reextração/anonimização silenciosa

#### Scenario: Texto anonimizado ou mapa alterado falha fechado
- **GIVEN** fonte principal inalterada, mas texto anonimizado ausente ou divergente, ou mapa de pseudônimos divergente
- **WHEN** o worker valida a confirmação
- **THEN** a confirmação não autoriza processamento, a invalidação é auditada e o caso vai a FAILED sem novo envio externo

#### Scenario: Alteração durante chamada em voo não autoriza etapa seguinte
- **GIVEN** uma chamada LLM iniciada com snapshot válido
- **WHEN** a fonte, o texto anonimizado ou o mapa muda antes da persistência
- **THEN** seu resultado não é persistido nem inicia outra chamada, e o caso vai a FAILED operacional; caso já encerrado permanece encerrado sem ressurgir

#### Scenario: Payload de autoridade inválido não vira confirmação implícita
- **GIVEN** registro de confirmação com versão, ator ou seleção inválidos
- **WHEN** o worker avalia sua autoridade
- **THEN** registra a invalidação e falha operacionalmente sem dispatch, em vez de inferir consentimento ou usar confirmação antiga

### Requirement: Recuperação observável de falha de enqueue após confirmação

Falha ao enfileirar reprocessamento após confirmação commitada SHALL preservar a confirmação e programar tentativa automática. O usuário SHALL ser informado se a recuperação foi programada ou depende de suporte. Falha de recuperação também SHALL ser observável. Nenhuma dessas falhas SHALL encaminhar o caso ao médico nem criar botão NIR de retry ou repetição infinita de análise.

#### Scenario: Falha de enqueue preserva confirmação e agenda recuperação
- **GIVEN** confirmação persistida e dispatch indisponível
- **WHEN** falha o enqueue
- **THEN** a confirmação e declaração permanecem, a recuperação e sua auditoria são persistidas em transação posterior ao commit da confirmação e a resposta redireciona com HTTP 302 e aviso de confirmação registrada/recuperação programada, sem mensagem de processamento concluído

#### Scenario: Falha dupla orienta suporte sem sucesso falso
- **GIVEN** falha de enqueue
- **WHEN** também falha a programação da recuperação
- **THEN** o caso não é encaminhado, a confirmação continua durável e o detalhe de erro HTTP 503 informa processamento pendente de suporte sem formulário de repetição nem dados clínicos; a falha de programação é auditada antes da resposta, sem sucesso falso

#### Scenario: Falha da transação de recovery e auditoria não reverte confirmação
- **GIVEN** falha de dispatch após confirmação commitada
- **WHEN** também falha a transação que persistiria recuperação e auditoria
- **THEN** a confirmação permanece, nenhum Schedule dessa transação é anunciado como persistido, um log técnico sanitizado registra a falha e a resposta HTTP 503 orienta suporte sem alegar auditoria gravada

#### Scenario: Recuperação executa pipeline, não PDF
- **GIVEN** recuperação pendente de um caso confirmado e ainda elegível ao processamento
- **WHEN** a tentativa programada é consumida
- **THEN** executa no cluster LLM sem extração PDF, respeita exclusividade e confirmação vigente e não duplica efeitos concluídos

#### Scenario: Job atrasado não recria trabalho concluído
- **GIVEN** recuperação que chega após conclusão, encerramento ou mudança de revisão
- **WHEN** o job é executado
- **THEN** não restaura declaração antiga, não reaplica confirmação inválida nem duplica encaminhamento
