# anonymization Specification (delta)

## Purpose

Barreira de privacidade do HMD: transformar o texto extraído do relatório em texto anonimizado com pseudônimos estáveis antes de qualquer chamada LLM, com pré-extração determinística dos identificadores de linkage, fail-closed em falhas e auditabilidade por caso.

## ADDED Requirements

### Requirement: Pré-extração determinística de identificadores

Antes da anonimização, o sistema SHALL extrair deterministicamente (regex) do texto extraído: o número de ocorrência, o nome do paciente, a data de nascimento e CPF/CNS quando presentes. Nome e nascimento SHALL ser persistidos no caso (campos de linkage); o número de ocorrência usa o campo existente. Os valores extraídos SHALL ser tratados como entidades garantidas na anonimização (anonimizados mesmo que o NLP não os detecte).

#### Scenario: Nome e nascimento extraídos e persistidos

- **GIVEN** um relatório com "Paciente: MARIA DA SILVA" e "Nascimento: 12/03/1960"
- **WHEN** a pré-extração determinística executa
- **THEN** o caso fica com `patient_name` = "MARIA DA SILVA" e `patient_birth_date` = 1960-03-12

#### Scenario: Valor determinístico é anonimizado mesmo sem NER

- **GIVEN** um nome de paciente que o modelo NLP não detecta como PERSON
- **WHEN** a anonimização executa com o merge determinístico
- **THEN** o nome ainda assim é substituído por token de pseudônimo no texto anonimizado

### Requirement: Anonimização com pseudônimos estáveis por caso

A anonimização SHALL combinar o analyzer Presidio pt-BR (modelo spaCy português) com recognizers brasileiros de CPF e CNS validados por checksum e CRM por padrão, aplicando substituição por tokens estáveis por caso (`<PESSOA_1>`, `<CPF_1>`, `<DATA_1>`, `<CRM_1>`, `<LOCAL_1>`, `<ORGANIZACAO_1>`, `<TELEFONE_1>`, `<EMAIL_1>`), numerados por ordem de primeira ocorrência e consistentes no documento. O texto anonimizado e o mapa (token → valor real + tipo) SHALL ser persistidos no caso, junto de relatório com contagens por tipo de entidade, modelo e versões do engine.

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
- **THEN** existe relatório com contagens por tipo de entidade, modelo NLP e versões, e evento na trilha com resumo

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

O sistema SHALL incluir um harness de benchmark que avalia um corpus (entradas com texto e valores esperados por tipo de entidade), reportando recall por tipo de entidade, contagens, latência p50/p95 e varredura zero-PII no output, com falha (exit ≠ 0) quando o recall fica abaixo do mínimo configurado. Um corpus sintético versionado acompanha a suíte de testes; a aceitação com relatórios reais é operacional (fora do CI).

#### Scenario: Benchmark reprova corpus abaixo do mínimo

- **GIVEN** um corpus com entidades esperadas e um mínimo de recall configurado
- **WHEN** o benchmark executa
- **THEN** o relatório mostra recall por entidade e o comando falha se algum tipo ficar abaixo do mínimo

#### Scenario: Corpus sintético passa no CI

- **GIVEN** o corpus sintético versionado
- **WHEN** o benchmark roda na suíte
- **THEN** recall ≥ mínimo e nenhuma PII verificável permanece no output
