# Delta: intake-nir

## MODIFIED Requirements

### Requirement: Extração assíncrona no cluster pdf

A criação SHALL enfileirar o processamento do caso no cluster `pdf` (django-q2); o processamento SHALL extrair o texto de cada documento com PyMuPDF na ordem declarada, concatenando em `Case.extracted_text` (fonte única de texto, com quebra de linha separando o fim de cada página), remover a marca d'água característica e extrair o número de ocorrência para `agency_record_number` quando presente. A marca d'água é um token de 5–6 dígitos — frequentemente DISTINTO do nº de ocorrência — nomeado quando aparece em ao menos 2 **linhas de marca**: linha composta exclusivamente de repetições do token (1 a N repetições separadas por espaço, nada mais na linha — cobre banda cheia, diagonal com poucas repetições e linha solta). As linhas de marca do token nomeado são descartadas; linha única não nomeia. O sweep além dessas linhas fica restrito ao token que coincide com o nº de ocorrência (removido em qualquer ocorrência delimitada do texto — o valor vive em `agency_record_number`); ocorrências de outros tokens nomeados no meio de linhas com outro conteúdo SHALL ser preservadas. Texto sem marca nomeada SHALL permanecer inalterado. A telemetria de remoção no evento de extração (sob a chave `watermark`) SHALL carregar somente contagens (tokens nomeados, linhas de marca descartadas, ocorrências varridas), jamais valores de token; o campo `record_number` pré-existente do evento (referência humana do caso, comportamento estabelecido antes deste change) permanece inalterado e fora dessa proibição — inclusive quando o token de marca coincide com o nº. O processamento SHALL ainda extrair os metadados do cabeçalho padrão SESAB (repetido por página) quando presentes — idade (`Idade: N a.` na linha de demografia), sexo, raça/cor e dias em tela (maior ocorrência de `Dias em tela: N`, com o valor na mesma linha OU na linha imediatamente seguinte quando o rótulo está sozinho — layout real do cabeçalho) e a **unidade de origem** (`Unid. Origem:`, valor na mesma linha OU na linha imediatamente seguinte) — persistindo-os nos campos do caso (`patient_age`/`patient_gender`/`patient_race`/`days_on_screen`/`origin_unit`), sem carregar esses valores em eventos. Campos ausentes do cabeçalho ficam vazios no caso. O ciclo SHALL percorrer `NEW → PDF_EXTRACTING → ANONYMIZING` com eventos de ator sistema, operando sob lock do caso (contexto de worker). Falha de extração (documento ilegível/corrompido) SHALL levar o caso a `FAILED` com motivo na trilha.

#### Scenario: Processamento feliz leva o caso a ANONYMIZING

- **GIVEN** um caso criado com PDFs válidos contendo o relatório padrão
- **WHEN** a task de processamento executa
- **THEN** o texto concatenado fica em `extracted_text`, o nº de ocorrência em `agency_record_number`, e o caso chega a `ANONYMIZING` com eventos `PDF_EXTRACTING` e da extração na trilha

#### Scenario: PDF corrompido leva a FAILED com motivo

- **GIVEN** um caso cujo documento não pode ser aberto pelo extrator
- **WHEN** a task executa
- **THEN** o caso transita para `FAILED` e o motivo da falha fica no payload do evento

#### Scenario: Marca d'água não vaza para o texto extraído

- **GIVEN** um relatório com a marca d'água característica (token de 5–6 dígitos, possivelmente distinto do nº de ocorrência) em ao menos 2 linhas de marca
- **WHEN** o texto é extraído
- **THEN** as linhas da marca não aparecem em `extracted_text` e o nº de ocorrência é capturado em `agency_record_number` quando presente

#### Scenario: Marca em linha solta ou com poucas repetições conta como linha de marca

- **GIVEN** um relatório cujo token de marca aparece como linha exata sozinha e/ou em linhas com poucas repetições (ex.: "48291 48291"), somando ao menos 2 linhas de marca
- **WHEN** o texto é extraído
- **THEN** essas linhas são removidas de `extracted_text` e o restante do texto clínico é preservado

#### Scenario: Marca d'água em linha solta repetida é removida

- **GIVEN** um relatório cuja marca de 5–6 dígitos aparece como linha exata sozinha (um token por linha, sem outro conteúdo), repetida ao menos 3× no documento
- **WHEN** o texto é extraído
- **THEN** essas linhas não aparecem em `extracted_text` e o restante do texto clínico é preservado

#### Scenario: Valor legítimo inline de token nomeado é preservado

- **GIVEN** um relatório com ao menos 2 linhas de marca do token "48291" (não coincidente com o nº de ocorrência) e uma linha clínica legítima citando "48291" no meio de outro conteúdo
- **WHEN** o texto é extraído
- **THEN** as linhas de banda são removidas e a ocorrência inline dentro da linha clínica permanece intacta

#### Scenario: Rastro do nº de ocorrência é varrido em qualquer lugar

- **GIVEN** um relatório cuja marca nomeada coincide com o nº de ocorrência (ao menos 2 linhas de marca) e que também cita o nº no meio de uma linha com outro conteúdo
- **WHEN** o texto é extraído
- **THEN** todas as ocorrências do nº saem de `extracted_text` e o valor vive em `agency_record_number`

#### Scenario: Linha de marca única não nomeia marca

- **GIVEN** um relatório com uma única linha composta de repetições de um token de 5–6 dígitos (ex.: valores iguais de tabela renderizados na mesma linha) e nenhuma outra linha de marca do mesmo token
- **WHEN** o texto é extraído
- **THEN** a linha permanece no `extracted_text` (nomeação exige ≥2 linhas de marca)

#### Scenario: Fronteira de página não funde linhas

- **GIVEN** um PDF multi-página cujo texto da primeira página não termina em quebra de linha e cuja segunda página começa com linha de marca
- **WHEN** o texto é extraído
- **THEN** a linha de marca da segunda página é reconhecida e removida (as páginas são concatenadas com quebra de linha)

#### Scenario: Evento de extração carrega contagens da remoção

- **GIVEN** um relatório processado com marca d'água removida
- **WHEN** o evento de extração concluída é gravado
- **THEN** o payload traz, sob `watermark`, somente contagens da remoção (a telemetria jamais carrega valores de token); o campo pré-existente `record_number` permanece com o nº quando capturado

#### Scenario: Texto sem marca permanece inalterado

- **GIVEN** um relatório sem marca d'água nomeável
- **WHEN** a limpeza executa
- **THEN** o texto entregue permanece idêntico ao extraído (sem normalização indevida)

#### Scenario: Metadados do cabeçalho populam o caso

- **GIVEN** um relatório com o cabeçalho padrão SESAB em cada página (linha de demografia com nome, `Idade: 79 a.`, sexo e raça/cor; `Dias em tela:` sozinho com `5` na linha seguinte na página 1 e `6` na última)
- **WHEN** a task de processamento executa
- **THEN** o caso fica com `patient_age`=79, `patient_gender`, `patient_race` e `days_on_screen`=6 (maior ocorrência) populados, e nenhum evento carrega esses valores

#### Scenario: Menção clínica de idade não vira metadado

- **GIVEN** um relatório cujo texto clínico contém «…Idade: 79a.» SEM os marcadores de sexo e raça/cor na mesma linha
- **WHEN** a extração de metadados executa
- **THEN** nenhum campo de metadado é populado por essa menção (a âncora exige a linha de demografia completa)

#### Scenario: Unidade de origem do cabeçalho popula o caso

- **GIVEN** um relatório com `Unid. Origem:` sozinho e o nome da unidade na linha imediatamente seguinte
- **WHEN** a task de processamento executa
- **THEN** o caso fica com `origin_unit` populado com o nome da unidade; rótulo sem valor deixa o campo vazio

#### Scenario: Cabeçalho sem metadados deixa campos vazios

- **GIVEN** um documento sem o cabeçalho padrão (texto legítimo, sem `Idade:`/`Dias em tela:`)
- **WHEN** a task de processamento executa
- **THEN** o caso é processado normalmente com os campos de metadados vazios (sem erro)
