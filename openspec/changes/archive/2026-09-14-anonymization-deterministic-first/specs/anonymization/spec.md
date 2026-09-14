# anonymization Specification (delta)

## MODIFIED Requirements

### Requirement: Anonimização com pseudônimos estáveis por caso

A anonimização SHALL aplicar substituição por tokens estáveis por caso
(`<PESSOA_1>`, `<CPF_1>`, `<DATA_1>`, `<OCORRENCIA_1>`), numerados por ordem
de primeira ocorrência e consistentes no documento, tendo a extração
determinística (rótulos do relatório e identificadores validados por
checksum: CPF/CNS, nascimento, nº de ocorrência) como camada primária
sempre ativa. O analyzer Presidio pt-BR (modelo spaCy português) com
recognizers brasileiros de CPF/CNS/CRM SHALL ser uma camada OPT-IN
(`ANONYMIZATION_USE_NER`, default desligado na fase 2), somando categorias
adicionais (`<CRM_1>`, `<LOCAL_1>`, `<ORGANIZACAO_1>`, `<TELEFONE_1>`,
`<EMAIL_1>`) quando habilitado; com ele habilitado, a combinação das camadas
preserva a vitória determinística em empates de offset. O texto anonimizado
e o mapa (token → valor real + tipo) SHALL ser persistidos no caso, junto de
relatório com contagens por tipo de entidade e a indicação de a camada NER
estava habilitada — com modelo e versões do engine QUANDO a camada NER
rodou (com NER desligado, o relatório omite esses campos por truthful).

#### Scenario: PII substituída por tokens estáveis

- **GIVEN** um texto com dois pacientes distintos citados e um CPF válido repetido
- **WHEN** a anonimização executa
- **THEN** cada valor distinto recebe um token único e estável, e as repetições do mesmo valor usam o mesmo token

#### Scenario: Texto anonimizado sem vestígios verificáveis

- **GIVEN** o texto anonimizado de um relatório com CPF/CNS válidos, nome e data
- **WHEN** varreduras de padrões (CPF/CNS com checksum) e busca pelos valores originais são aplicadas
- **THEN** nenhuma ocorrência é encontrada

#### Scenario: Relatório de anonimização auditável

- **GIVEN** uma anonimização concluída
- **WHEN** o caso é inspecionado
- **THEN** existe relatório com contagens por tipo de entidade, indicação da
  camada NER e — quando ela rodou — modelo NLP e versões; e evento na trilha
  com resumo

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
