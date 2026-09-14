# anonymization Specification (delta)

## ADDED Requirements

### Requirement: Anonimização determinística-first (fase 2)

A camada de anonimização aplicada aos casos SHALL ter a extração
determinística (rótulos do relatório + runs de identificadores) como camada
primária e suficiente para a identidade do paciente, tokenizando todas as
ocorrências dos valores extraídos. A camada NER/Presidio SHALL ser opcional
(`ANONYMIZATION_USE_NER`, default desligado) e, quando desligada, o engine
não SHALL ser construído nem consultado. O motivo de negatura consultado no
pipeline SHALL ser anonimizado no espaço de tokens do caso (semeadura pelo
`pseudonym_map` do caso anterior). O guard de tokens do pipeline SHALL
permanecer ativo sobre o mapa resultante. A decisão de operar com terceiros
em texto claro (postura da referência ats-web) fica registrada como escolha
de política do dono, reversível por variável de ambiente acompanhada de
calibração pelo benchmark.

#### Scenario: NER desligado por default anonimiza só o determinístico

- **GIVEN** um relatório com rótulos de paciente e corpo contendo vocabulário
  clínico antes classificado como PESSOA/LOCAL pelo NER
- **WHEN** a anonimização é executada com o setting default
- **THEN** o pseudonym_map contém apenas as entradas determinísticas
  (nome/CNS/CPF/nascimento/nº de ocorrência do paciente) e nenhum termo
  clínico recebe token

#### Scenario: Engine Presidio não é construído com NER desligado

- **GIVEN** o setting `ANONYMIZATION_USE_NER` desligado
- **WHEN** a anonimização é executada
- **THEN** o analyzer/estrutura do Presidio não é instanciado nem consultado

#### Scenario: Nome do paciente no corpo inteiro é tokenizado sem NER

- **GIVEN** um relatório cujo nome do paciente aparece no rótulo E repetido
  no texto corrido
- **WHEN** a anonimização é executada com NER desligado
- **THEN** todas as ocorrências do nome recebem o mesmo token de PESSOA

#### Scenario: Reativação do NER é reversível por env

- **GIVEN** o setting `ANONYMIZATION_USE_NER=true` no worker responsável
- **WHEN** a anonimização é executada
- **THEN** os candidatos do NER voltam a ser somados aos determinísticos
  (merge com vitória determinística em empate preservada)

#### Scenario: Motivo de negatura é anonimizado com o mapa do caso

- **GIVEN** um caso anterior encerrado por negatura cujo motivo cita o nome
  do paciente, consultado pelo pipeline de caso novo
- **WHEN** o motivo é preparado para o LLM
- **THEN** o nome do paciente é substituído pelo token do caso (semeadura
  pelo `pseudonym_map` do caso anterior), mesmo sem rótulo SESAB no motivo

#### Scenario: Guard de tokens passa com mapa determinístico limpo

- **GIVEN** um caso anonimizado apenas pela camada determinística cujo corpo
  contém vocabulário clínico comum e um artefato de LLM que discute esses
  termos
- **WHEN** o guard de tokens valida o artefato
- **THEN** nenhum valor real do mapa aparece e o pipeline prossegue (o caso
  do incidente 3ca56d86 teria passado)

#### Scenario: Benchmark continua calibrando a camada NER

- **GIVEN** o comando `anonymization_benchmark` executado com o setting
  `ANONYMIZATION_USE_NER=true`
- **WHEN** o corpus sintético (incluindo nome adversarial) é avaliado
- **THEN** o recall por tipo continua sendo reportado como critério de
  calibração da reativação futura
