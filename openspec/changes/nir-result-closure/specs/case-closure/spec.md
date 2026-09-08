# case-closure Specification (delta)

## Purpose

Fechar o ciclo do caso após a resposta final — ciência do NIR com minimização de dados clínicos, visão de resultado e de casos encerrados para o remetente, e reenvio corrigido como novo caso vinculado — com trilha auditável e sem quebrar o prior-case.

## ADDED Requirements

### Requirement: Resposta final de negativa médica

Quando o médico nega todos os procedimentos declarados, o sistema SHALL publicar automaticamente a resposta final ao NIR na thread do caso — com o motivo de cada procedimento negado — levando o caso a `FINAL_REPLY_POSTED` no mesmo atomic. Decisão parcial (ao menos um procedimento aprovado) SHALL seguir para o agendamento sem publicar resposta final.

#### Scenario: Negação total publica resposta com motivos

- **GIVEN** um caso em `AWAITING_DOCTOR` com dois procedimentos declarados
- **WHEN** o médico nega ambos informando os motivos
- **THEN** o caso chega a `FINAL_REPLY_POSTED` e a thread contém a resposta final citando cada procedimento negado com seu motivo

#### Scenario: Decisão parcial não publica resposta final

- **GIVEN** um caso em `AWAITING_DOCTOR` com dois procedimentos declarados
- **WHEN** o médico aprova um e nega outro
- **THEN** o caso segue para `SCHEDULER_REQUESTED` e nenhuma resposta final é publicada

#### Scenario: Estado fora da negativa é recusado

- **GIVEN** um caso que não está em `DOCTOR_DENIED`
- **WHEN** o serviço de resposta de negativa é invocado
- **THEN** a operação é recusada com erro nomeado e nada muda

### Requirement: Ciência do NIR com limpeza de dados clínicos

O criador do caso SHALL poder confirmar o recebimento da resposta final, levando o caso de `FINAL_REPLY_POSTED` a `CLEANED` de forma transacional: documentos originais (rows e arquivos), texto extraído, texto anonimizado, mapa de pseudônimos e artefatos do pipeline (estrutura, sumário, sugestão, policy) são removidos; identificação do paciente, decisões médicas, trilha de eventos, comunicações e dados de agendamento são preservados. Um caso encerrado dentro da janela SHALL continuar elegível como prior-case.

#### Scenario: Ciência encerra e limpa o caso

- **GIVEN** um caso em `FINAL_REPLY_POSTED` criado pelo NIR autenticado, com documentos, artefatos do pipeline e mapa de pseudônimos
- **WHEN** ele confirma o recebimento
- **THEN** o caso chega a `CLEANED`, os documentos deixam de existir (rows e acesso), e texto extraído/anonimizado, mapa de pseudônimos e artefatos do pipeline estão vazios

#### Scenario: Essenciais preservados após a limpeza

- **GIVEN** um caso recém-encerrado pela ciência do NIR
- **WHEN** o detalhe do caso é consultado
- **THEN** identificação do paciente, decisões médicas por procedimento, trilha de eventos, comunicações e dados de agendamento continuam presentes

#### Scenario: Caso encerrado segue elegível como prior-case

- **GIVEN** um caso encerrado (`CLEANED`) há menos de sete dias com decisões médicas registradas
- **WHEN** um novo caso do mesmo tipo/paciente consulta o contexto de casos anteriores
- **THEN** o caso encerrado é considerado pelo prior-case com suas decisões

#### Scenario: Documento de caso encerrado é inacessível

- **GIVEN** um caso encerrado pela ciência
- **WHEN** qualquer rota de documento do caso é acessada
- **THEN** retorna HTTP 404

#### Scenario: Estado fora da ciência é recusado

- **GIVEN** um caso que não está em `FINAL_REPLY_POSTED`
- **WHEN** a ciência é invocada
- **THEN** a operação é recusada com erro nomeado e nada muda

### Requirement: Resultado e casos encerrados visíveis ao criador

O detalhe do caso para o NIR SHALL exibir, a partir da decisão médica, o resultado (decisões por procedimento com motivos, dados de agendamento e a resposta final em destaque) e um meio de confirmar o recebimento apenas enquanto o caso estiver em `FINAL_REPLY_POSTED` e for de sua autoria. A lista "meus casos" SHALL separar ativos de encerrados.

#### Scenario: Resultado exibido ao criador

- **GIVEN** um caso com resposta final publicada, criado pelo NIR autenticado
- **WHEN** ele abre o detalhe do caso
- **THEN** decisões com motivos, dados de agendamento e a resposta final são exibidos, junto ao botão de confirmação de recebimento

#### Scenario: Ciência apenas do criador no estado certo

- **GIVEN** um caso em `FINAL_REPLY_POSTED` criado por outro NIR e um caso do NIR autenticado já encerrado
- **WHEN** ele tenta confirmar recebimento de qualquer um deles
- **THEN** recebe 404 no caso alheio e mensagem de erro no caso já encerrado, sem mudança de estado

#### Scenario: Aba de encerrados lista apenas casos CLEANED

- **GIVEN** casos do NIR em estados variados, incluindo encerrados
- **WHEN** ele abre a aba de encerrados em "meus casos"
- **THEN** apenas os casos `CLEANED` de sua autoria são listados

### Requirement: Reenvio corrigido cria novo caso vinculado

O sistema SHALL permitir ao NIR reenviar, de forma corrigida, um caso seu encerrado: cria um novo caso que percorre o pipeline completo desde o início, vinculado ao original com motivo obrigatório e tipos declarados explícitos (nunca herdados). O caso original SHALL permanecer inalterado exceto pelo evento de supersedição e pela listagem dos reenvios que o corrigem.

#### Scenario: Reenvio cria novo caso vinculado com motivo

- **GIVEN** um caso encerrado criado pelo NIR autenticado
- **WHEN** ele reenvia de forma corrigida com novos documentos, tipos declarados e motivo
- **THEN** um novo caso é criado em processamento inicial vinculado ao original, e ambos registram os eventos de correção e supersedição

#### Scenario: Tipos do reenvio não são herdados

- **GIVEN** um caso encerrado cujo tipo original é um subtipo qualquer
- **WHEN** o NIR reenvia declarando tipos diferentes dos originais
- **THEN** o novo caso registra exatamente os tipos declarados no reenvio

#### Scenario: Original permanece encerrado e íntegro

- **GIVEN** um caso encerrado que sofreu um reenvio corrigido
- **WHEN** o original é consultado
- **THEN** permanece `CLEANED` com seus dados intactos, listando o reenvio que o corrige

#### Scenario: Reenvio exige caso encerrado próprio

- **GIVEN** um caso não encerrado e um caso encerrado criado por outro NIR
- **WHEN** o NIR autenticado tenta o reenvio corrigido de qualquer um deles
- **THEN** ambos são recusados com erro apropriado e nenhum caso é criado
