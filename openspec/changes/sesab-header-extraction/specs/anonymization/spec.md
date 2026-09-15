# Delta: anonymization

## MODIFIED Requirements

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

- **GIVEN** um relatório real SESAB cujo texto linear traz o nome na linha de demografia (linha anterior ao rótulo `Paciente:` sozinho, com `Idade: N a.` na mesma linha) repetido por página
- **WHEN** a pré-extração determinística executa
- **THEN** `patient_name` fica populado com o nome completo e TODAS as ocorrências do nome (em todas as páginas) recebem o mesmo token `<PESSOA_N>` na anonimização

#### Scenario: Nome social presente vira candidato PESSOA próprio

- **GIVEN** um relatório com nome civil no cabeçalho e nome social preenchido em outro campo
- **WHEN** a pré-extração e a anonimização executam
- **THEN** o nome social recebe token `<PESSOA_M>` distinto do token do nome civil e o linkage usa apenas o nome civil

#### Scenario: Campos vazios do cabeçalho não geram candidatos

- **GIVEN** um relatório SESAB real com `Nome Social:` vazio e sem data de nascimento
- **WHEN** a pré-extração determinística executa
- **THEN** não há candidato de nome social nem de nascimento e o linkage do nome civil é populado normalmente
