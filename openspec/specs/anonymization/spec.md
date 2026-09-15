# anonymization Specification

## Purpose

Barreira de privacidade do HMD: transformar o texto extraído do relatório em texto anonimizado com pseudônimos estáveis antes de qualquer chamada LLM, com pré-extração determinística dos identificadores de linkage — incluindo o nome do paciente do cabeçalho padrão SESAB (layout desalinhado da extração linear do PDF) — fail-closed em falhas e auditabilidade por caso.

## Requirements

### Requirement: Pré-extração determinística de identificadores

Antes da anonimização, o sistema SHALL extrair deterministicamente (regex) do texto extraído: o número de ocorrência, o nome do paciente, a data de nascimento e CPF/CNS quando presentes. O nome do paciente SHALL ser extraído do cabeçalho padrão SESAB — valor desalinhado do rótulo `Paciente:` na extração linear do PDF, âncora na linha de demografia (`<nome> - Idade: N a. - Sexo: ... - Raça/Cor: ...` na linha anterior ao rótulo sozinho) — mantendo os patterns de rótulo-na-mesma-linha para outros layouts. O nome social, quando presente, SHALL ser extraído como candidato PESSOA (token próprio, distinto do nome civil), sem alimentar o linkage. Nome e nascimento SHALL ser persistidos no caso (campos de linkage); o número de ocorrência usa o campo existente. Os valores extraídos SHALL ser tratados como entidades garantidas na anonimização (anonimizados mesmo que o NLP não os detecte).

#### Scenario: Nome e nascimento extraídos e persistidos

- **GIVEN** um relatório com "Paciente: MARIA DA SILVA" e "Nascimento: 12/03/1960"
- **WHEN** a pré-extração determinística executa
- **THEN** o caso fica com `patient_name` = "MARIA DA SILVA" e `patient_birth_date` = 1960-03-12

#### Scenario: Valor determinístico é anonimizado mesmo sem NER

- **GIVEN** um nome de paciente que o modelo NLP não detecta como PERSON
- **WHEN** a anonimização executa com o merge determinístico
- **THEN** o nome ainda assim é substituído por token de pseudônimo no texto anonimizado

#### Scenario: Nome do cabeçalho padrão SESAB é extraído do layout desalinhado

- **GIVEN** um relatório real SESAB cujo texto linear traz o nome na linha de demografia (linha anterior ao rótulo `Paciente:` sozinho, com `Idade:`, `Sexo:` e `Raça/Cor:` juntos na mesma linha) repetido por página
- **WHEN** a pré-extração determinística executa
- **THEN** `patient_name` fica populado com o nome completo e TODAS as ocorrências do nome (em todas as páginas) recebem o mesmo token `<PESSOA_N>` na anonimização

#### Scenario: Demografia clínica órfã não gera nome

- **GIVEN** um texto clínico contendo «…Idade: 79a.» SEM os marcadores de sexo e raça/cor na mesma linha, seguido de uma linha «Paciente:»
- **WHEN** a pré-extração determinística executa
- **THEN** nenhum nome é extraído dessa menção (a âncora exige a linha de demografia completa) e o texto segue ao LLM como está

#### Scenario: Nome social presente vira candidato PESSOA próprio

- **GIVEN** um relatório com nome civil no cabeçalho e nome social preenchido em outro campo
- **WHEN** a pré-extração e a anonimização executam
- **THEN** o nome social recebe token `<PESSOA_M>` distinto do token do nome civil e o linkage usa apenas o nome civil

#### Scenario: Campos vazios do cabeçalho não geram candidatos

- **GIVEN** um relatório SESAB real com `Nome Social:` vazio (rótulo sozinho precedido por texto clínico) e sem data de nascimento
- **WHEN** a pré-extração determinística executa
- **THEN** não há candidato de nome social nem de nascimento e o linkage do nome civil é populado normalmente (a captura do nome social restringe-se ao valor na mesma linha do rótulo)

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

### Requirement: Fail-closed antes de qualquer LLM

Falha em qualquer etapa da anonimização SHALL levar o caso a `FAILED` com motivo na trilha, e o texto anonimizado permanece vazio. O pipeline SHALL considerar a anonimização bem-sucedida (texto anonimizado não-vazio) como pré-condição inabalável para qualquer chamada LLM; texto real nunca é enviado ao LLM.

#### Scenario: Falha na anonimização bloqueia o pipeline

- **GIVEN** um caso em `ANONYMIZING` cuja anonimização levanta exceção
- **WHEN** a task processa o caso
- **THEN** o caso vai a `FAILED` com motivo no evento e permanece sem texto anonimizado — nenhuma etapa posterior executa

### Requirement: Processamento assíncrono no cluster anonymization

Ao entrar em `ANONYMIZING`, o caso SHALL ter o processamento de anonimização enfileirado no cluster `anonymization` (worker dedicado, engine singleton por processo). A task SHALL ser idempotente por estado (apenas casos em `ANONYMIZING`; demais estados → no-op), operar sob lock do caso (contexto de worker), registrar início/conclusão na trilha e avançar o caso para `LLM_EXTRACTING` apenas em sucesso.

#### Scenario: Caso que entra em ANONYMIZING é anonimizado

- **GIVEN** um caso que chega a `ANONYMIZING` com texto extraído
- **WHEN** a task de anonimização executa
- **THEN** o caso fica com texto anonimizado/mapa/relatório e avança para `LLM_EXTRACTING` com eventos de início e conclusão

#### Scenario: Reexecução não duplica efeitos

- **GIVEN** um caso já em `LLM_EXTRACTING`
- **WHEN** a task executa novamente
- **THEN** é no-op sem novos eventos ou alterações

### Requirement: Re-identificação controlada por caso

O sistema SHALL oferecer re-identificação de texto por caso (substituição de tokens pelos valores reais do mapa), como serviço para os presenters autorizados. O controle de acesso (quem pode re-identificar) é responsabilidade dos consumers; este change entrega a mecânica fiel.

#### Scenario: Roundtrip de re-identificação

- **GIVEN** o texto anonimizado de um caso e seu mapa de pseudônimos
- **WHEN** a re-identificação executa sobre o texto anonimizado
- **THEN** o resultado reproduz os valores originais substituídos por tokens

### Requirement: Benchmark como critério de aceite técnico

O sistema SHALL incluir um harness de benchmark que avalia um corpus (entradas com texto e valores esperados por tipo de entidade), reportando recall por tipo de entidade, contagens, latência p50/p95, varredura zero-PII no output, pico de memória do processo e documentos bloqueados, com falha (exit ≠ 0) quando o recall fica abaixo do mínimo configurado, houver vestígio de PII ou documento bloqueado. Um corpus sintético versionado acompanha a suíte de testes; a aceitação com relatórios reais é operacional (fora do CI).

#### Scenario: Benchmark reprova corpus abaixo do mínimo

- **GIVEN** um corpus com entidades esperadas e um mínimo de recall configurado
- **WHEN** o benchmark executa
- **THEN** o relatório mostra recall por entidade e o comando falha se algum tipo ficar abaixo do mínimo

#### Scenario: Corpus sintético passa no CI

- **GIVEN** o corpus sintético versionado
- **WHEN** o benchmark roda na suíte
- **THEN** recall ≥ mínimo e nenhuma PII verificável permanece no output

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
