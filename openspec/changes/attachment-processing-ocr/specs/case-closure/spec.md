# case-closure Specification (delta)

## MODIFIED Requirements

### Requirement: Ciência do NIR com limpeza de dados clínicos

O criador do caso SHALL poder confirmar o recebimento da resposta final, levando o caso de `FINAL_REPLY_POSTED` a `CLEANED` de forma transacional: documentos originais e anexos (rows e arquivos), texto extraído, texto anonimizado, mapa de pseudônimos e artefatos do pipeline (estrutura, sumário, sugestão, policy) são removidos — inclusive o conteúdo e os mapas dos anexos; identificação do paciente, decisões médicas, trilha de eventos, comunicações e dados de agendamento são preservados. Um caso encerrado dentro da janela SHALL continuar elegível como prior-case.

#### Scenario: Ciência encerra e limpa o caso

- **GIVEN** um caso em `FINAL_REPLY_POSTED` criado pelo NIR autenticado, com documentos, artefatos do pipeline e mapa de pseudônimos
- **WHEN** ele confirma o recebimento
- **THEN** o caso chega a `CLEANED`, os documentos deixam de existir (rows e acesso), e texto extraído/anonimizado, mapa de pseudônimos e artefatos do pipeline estão vazios

#### Scenario: Essenciais preservados após a limpeza

- **GIVEN** um caso recém-encerrado pela ciência do NIR
- **WHEN** o detalhe do caso é consultado
- **THEN** identificação do paciente, decisões médicas por procedimento, trilha de eventos, comunicações e dados de agendamento continuam presentes

#### Scenario: Caso encerrado segue elegível como prior-case

- **GIVEN** um caso encerrado (`CLEANED`) cuja decisão médica mais recente foi registrada dentro da janela do prior-case
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

#### Scenario: Anexos são removidos na ciência

- **GIVEN** um caso em `FINAL_REPLY_POSTED` com anexos processados (arquivos, textos extraídos/anonimizados e mapas de pseudônimos dos anexos)
- **WHEN** o criador confirma o recebimento
- **THEN** as rows e os arquivos dos anexos deixam de existir, junto com os documentos e artefatos do caso
