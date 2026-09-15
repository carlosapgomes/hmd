# Delta: intake-nir

## MODIFIED Requirements

### Requirement: Extração assíncrona no cluster pdf

A criação SHALL enfileirar o processamento do caso no cluster `pdf` (django-q2); o processamento SHALL extrair o texto de cada documento com PyMuPDF na ordem declarada, concatenando em `Case.extracted_text` (fonte única de texto), remover a marca d'água característica e extrair o número de ocorrência para `agency_record_number` quando presente. O processamento SHALL ainda extrair os metadados do cabeçalho padrão SESAB (repetido por página) quando presentes — idade, sexo e raça/cor extraídos SOMENTE da linha de demografia canônica (os marcadores `Idade:`, `Sexo:` e `Raça/Cor:` juntos na mesma linha, na ordem; raça restrita à enumeração IBGE + «Não informado») e dias em tela (maior ocorrência de `Dias em tela: N`) — persistindo-os nos campos do caso (`patient_age`/`patient_gender`/`patient_race`/`days_on_screen`), sem carregar esses valores em eventos. Campos ausentes do cabeçalho ficam vazios no caso; menções clínicas isoladas de idade/sexo NÃO são metadados. O ciclo SHALL percorrer `NEW → PDF_EXTRACTING → ANONYMIZING` com eventos de ator sistema, operando sob lock do caso (contexto de worker). Falha de extração (documento ilegível/corrompido) SHALL levar o caso a `FAILED` com motivo na trilha.

#### Scenario: Processamento feliz leva o caso a ANONYMIZING

- **GIVEN** um caso criado com PDFs válidos contendo o relatório padrão
- **WHEN** a task de processamento executa
- **THEN** o texto concatenado fica em `extracted_text`, o nº de ocorrência em `agency_record_number`, e o caso chega a `ANONYMIZING` com eventos `PDF_EXTRACTING` e da extração na trilha

#### Scenario: PDF corrompido leva a FAILED com motivo

- **GIVEN** um caso cujo documento não pode ser aberto pelo extrator
- **WHEN** a task executa
- **THEN** o caso transita para `FAILED` e o motivo da falha fica no payload do evento

#### Scenario: Marca d'água não vaza para o texto extraído

- **GIVEN** um relatório com a marca d'água característica (nº do registro repetido)
- **WHEN** o texto é extraído
- **THEN** a sequência da marca d'água não aparece em `extracted_text` e o nº é capturado como `agency_record_number`

#### Scenario: Metadados do cabeçalho populam o caso

- **GIVEN** um relatório com o cabeçalho padrão SESAB em cada página (linha de demografia com nome, `Idade: 79 a.`, sexo e raça/cor; `Dias em tela: 5` na página 1 e `Dias em tela: 6` na última)
- **WHEN** a task de processamento executa
- **THEN** o caso fica com `patient_age`=79, `patient_gender`, `patient_race` e `days_on_screen`=6 (maior ocorrência) populados, e nenhum evento carrega esses valores

#### Scenario: Cabeçalho sem metadados deixa campos vazios

- **GIVEN** um documento sem o cabeçalho padrão (texto legítimo, sem `Idade:`/`Dias em tela:`)
- **WHEN** a task de processamento executa
- **THEN** o caso é processado normalmente com os campos de metadados vazios (sem erro)

#### Scenario: Menção clínica de idade não vira metadado

- **GIVEN** um relatório cujo texto clínico contém «…Idade: 79a.» SEM os marcadores de sexo e raça/cor na mesma linha
- **WHEN** a extração de metadados executa
- **THEN** nenhum campo de metadado é populado por essa menção (a âncora exige a linha de demografia completa)
