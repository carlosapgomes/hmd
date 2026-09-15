# intake-nir Specification

## Purpose

Porta de entrada de casos do HMD: o NIR envia os PDFs do relatório de regulação e declara os tipos de procedimento; o sistema extrai o texto de forma assíncrona — incluindo os metadados do cabeçalho padrão SESAB (idade, sexo, raça/cor, dias em tela) — aplica o gate de regulação adaptado (retendo documentos fora do padrão para revisão) e mantém o NIR informado pelos meus casos.

## Requirements

### Requirement: Criação de caso com upload multi-PDF e declaração de tipos

O NIR (papel ativo `nir`) SHALL criar um caso enviando de 1 a N arquivos PDF que compõem o relatório de regulação e declarando ao menos um tipo de procedimento do catálogo. A criação SHALL ser atômica: caso em `NEW`, documentos ordenados e declaração de procedimentos ocorrem juntos ou nada ocorre. Arquivos não-PDF ou acima dos limites configurados SHALL ser rejeitados com erro claro, sem criar o caso; tipos fora do catálogo rejeitados pela validação existente.

#### Scenario: Criação com múltiplos PDFs e tipos declarados

- **GIVEN** um usuário com papel ativo `nir`
- **WHEN** envia 2 PDFs válidos e declara `art_perif` e `cat_cardiaco`
- **THEN** o caso nasce em `NEW` com 2 documentos ordenados e exatamente os dois tipos declarados, com evento de declaração na trilha

#### Scenario: Arquivo não-PDF rejeita a criação inteira

- **GIVEN** um usuário com papel ativo `nir`
- **WHEN** envia 1 PDF e 1 imagem (não-PDF) com tipos válidos
- **THEN** nenhum caso é criado e o erro identifica o arquivo inválido

#### Scenario: Limite de quantidade excede

- **GIVEN** a configuração de limite de documentos por caso
- **WHEN** o NIR envia mais arquivos que o limite
- **THEN** a criação é rejeitada com erro claro antes de qualquer persistência

### Requirement: Extração assíncrona no cluster pdf

A criação SHALL enfileirar o processamento do caso no cluster `pdf` (django-q2); o processamento SHALL extrair o texto de cada documento com PyMuPDF na ordem declarada, concatenando em `Case.extracted_text` (fonte única de texto), remover a marca d'água característica e extrair o número de ocorrência para `agency_record_number` quando presente. O processamento SHALL ainda extrair os metadados do cabeçalho padrão SESAB (repetido por página) quando presentes — idade, sexo e raça/cor extraídos SOMENTE da linha de demografia canônica (os marcadores `Idade:`, `Sexo:` e `Raça/Cor:` juntos na mesma linha, na ordem; raça restrita à enumeração IBGE + «Não informado») e dias em tela (maior ocorrência do marcador `Dias em tela`, com o valor na mesma linha OU na linha imediatamente seguinte quando o rótulo está sozinho — layout real do cabeçalho) — persistindo-os nos campos do caso (`patient_age`/`patient_gender`/`patient_race`/`days_on_screen`), sem carregar esses valores em eventos. Campos ausentes do cabeçalho ficam vazios no caso; menções clínicas isoladas de idade/sexo NÃO são metadados. O ciclo SHALL percorrer `NEW → PDF_EXTRACTING → ANONYMIZING` com eventos de ator sistema, operando sob lock do caso (contexto de worker). Falha de extração (documento ilegível/corrompido) SHALL levar o caso a `FAILED` com motivo na trilha.

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

- **GIVEN** um relatório com o cabeçalho padrão SESAB em cada página (linha de demografia com nome, `Idade: 79 a.`, sexo e raça/cor; `Dias em tela:` sozinho com `5` na linha seguinte na página 1 e `6` na última)
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

### Requirement: Gate de regulação retém documento fora do padrão

O processamento SHALL validar o documento contra o padrão do relatório de regulação (cabeçalho "RELATÓRIO DE OCORRÊNCIAS", sinais institucionais e seções operacionais, com limites configuráveis por ambiente). Documento fora do padrão SHALL deixar o caso **retido**: permanece em `PDF_EXTRACTING` com `manual_review_required=True` e motivo registrado em evento — e **nunca** avança sozinho para as etapas seguintes. Documento dentro do padrão segue normalmente.

#### Scenario: Documento fora do padrão fica retido

- **GIVEN** um caso cujo texto extraido não contém os sinais do padrão
- **WHEN** a task executa o gate
- **THEN** o caso permanece em `PDF_EXTRACTING` com `manual_review_required=True` e evento com o motivo da retenção

#### Scenario: Documento no padrão segue

- **GIVEN** um caso cujo texto contém cabeçalho e seções suficientes
- **WHEN** a task executa o gate
- **THEN** o caso avança para `ANONYMIZING` sem flag de revisão

### Requirement: Revisão NIR do gate

O NIR SHALL conseguir revisar casos retidos pelo gate na tela de detalhe, com duas ações: **liberar** (bypass — o caso avança para `ANONYMIZING` com evento de bypass registrado) e **reenviar documentos** (substitui os PDFs do caso e reprocessa do início da extração, zerando a flag e o texto anterior). Ambas as ações ficam disponíveis apenas enquanto o caso estiver retido.

#### Scenario: Liberar avança com evento de bypass

- **GIVEN** um caso retido em `PDF_EXTRACTING` com `manual_review_required=True`
- **WHEN** o NIR libera o caso
- **THEN** o caso avança para `ANONYMIZING` e a trilha registra o evento de bypass com o NIR como ator

#### Scenario: Reenviar documentos reprocessa do zero

- **GIVEN** um caso retido com documentos inválidos
- **WHEN** o NIR reenvia novos PDFs válidos
- **THEN** os documentos antigos são substituídos, a flag de revisão é zerada, o texto anterior é descartado e o caso é reprocessado

### Requirement: Meus casos e detalhe do NIR

O NIR SHALL ver, numa lista "meus casos", apenas os casos criados por ele, com status, tipos declarados, indicador de retenção pelo gate e a identificação do paciente (nome e idade quando presentes — o NIR é o criador do caso); o detalhe de um caso SHALL exibir documentos (com visualização do PDF), trilha de eventos e comunicações. Acesso a caso criado por outro usuário SHALL ser negado.

#### Scenario: Lista mostra apenas casos do próprio NIR

- **GIVEN** dois NIR com casos criados
- **WHEN** um deles abre meus casos
- **THEN** vê somente os casos que ele criou, com status, flag de retenção e identificação do paciente visíveis

#### Scenario: Detalhe de caso alheio é negado

- **GIVEN** um caso criado por outro NIR
- **WHEN** o NIR tenta abrir o detalhe diretamente
- **THEN** o acesso é negado (404) sem vazar informação

#### Scenario: Detalhe exibe documentos e trilha

- **GIVEN** um caso do próprio NIR em processamento
- **WHEN** abre o detalhe
- **THEN** vê os PDFs enviados (servidos de forma segura), a trilha de eventos e as comunicações do caso

#### Scenario: Card de meus casos identifica o paciente

- **GIVEN** um caso criado pelo NIR com nome e idade extraídos do cabeçalho
- **WHEN** o NIR abre meus casos
- **THEN** o card exibe o nome do paciente e a idade (`84 a`); caso sem identificação exibe `—` no lugar do nome e omite a idade
