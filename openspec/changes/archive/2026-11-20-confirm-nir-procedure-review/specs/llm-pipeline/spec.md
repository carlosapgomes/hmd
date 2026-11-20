# Spec Delta

## MODIFIED Requirements

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

## ADDED Requirements

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
