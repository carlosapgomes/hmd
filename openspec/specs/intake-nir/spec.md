# intake-nir Specification

## Purpose

Porta de entrada de casos do HMD: o NIR envia os PDFs do relatório de regulação e declara os tipos de procedimento; o sistema extrai o texto de forma assíncrona — incluindo os metadados do cabeçalho padrão SESAB (idade, sexo, raça/cor, dias em tela) — aplica o gate de regulação adaptado (retendo documentos fora do padrão para revisão) e mantém o NIR informado pelos meus casos.

## Requirements

### Requirement: Envio em lote: um PDF por caso, tipo único

O NIR (papel ativo `nir`) SHALL enviar de 1 a N arquivos PDF num único envio, onde cada PDF é o relatório de um paciente e vira um caso independente. Cada caso SHALL nascer atomicamente (caso em `NEW`, exatamente 1 documento, declaração do único tipo do lote e eventos juntos ou nada). O tipo de procedimento SHALL ser único por envio/lote (validado contra o catálogo; múltiplos tipos não são permitidos). O lote SHALL respeitar limites configurados de contagem de arquivos, tamanho por arquivo e tamanho total, rejeitados com erro claro antes de qualquer persistência. Falhas por arquivo (não-PDF, tamanho) SHALL rejeitar apenas aquele arquivo: os válidos viram casos e o resultado lista os casos criados e os erros por arquivo; exceção de persistência num caso do lote SHALL resultar em erro por arquivo, preservando os casos já criados. Anexos seguem a regra da spec `attachments` (somente com exatamente 1 PDF; no lote multi-PDF com anexos enviados, os casos são criados sem anexos e o erro informa a restrição). O envio com exatamente 1 PDF e anexo inválido SHALL abortar todo o envio (nada criado).

#### Scenario: Lote de dois PDFs cria dois casos independentes

- **GIVEN** um usuário com papel ativo `nir`
- **WHEN** envia 2 PDFs válidos declarando um único tipo do catálogo
- **THEN** dois casos independentes nascem em `NEW`, cada um com exatamente 1 documento e o tipo declarado, com evento de declaração por caso na trilha

#### Scenario: Arquivo inválido no lote rejeita apenas ele

- **GIVEN** um lote com 2 PDFs válidos e 1 imagem (não-PDF)
- **WHEN** o envio é submetido
- **THEN** os 2 casos dos PDFs válidos são criados e o resultado lista o erro nomeado do arquivo inválido, sem interromper os demais

#### Scenario: Tipo único inválido rejeita o lote inteiro

- **GIVEN** um envio com tipo fora do catálogo (ou mais de um tipo)
- **WHEN** o envio é submetido
- **THEN** nenhum caso é criado e o erro informa a restrição de tipo único

#### Scenario: Limites do lote excedidos rejeitam tudo

- **GIVEN** a configuração de limites de contagem/tamanho do lote
- **WHEN** o envio excede arquivos por lote ou tamanho total
- **THEN** nada é criado e o erro informa o limite violado antes de qualquer persistência

#### Scenario: Enviar um único PDF com anexo inválido aborta tudo

- **GIVEN** um envio com exatamente 1 PDF válido e 1 anexo fora dos limites
- **WHEN** o envio é submetido
- **THEN** nenhum caso é criado e o erro nomeado do anexo é retornado

#### Scenario: Lote multi-PDF com anexos cria casos sem anexos

- **GIVEN** um envio com 2 PDFs válidos e anexos
- **WHEN** o envio é submetido
- **THEN** os 2 casos são criados sem anexos e o resultado informa que anexos só são permitidos com exatamente 1 relatório

#### Scenario: Cada caso do lote é atomicamente criado

- **GIVEN** um lote em que o 2º arquivo falha na persistência após o 1º caso já criado
- **WHEN** o envio é processado
- **THEN** o 1º caso permanece criado e íntegro e o resultado lista o erro do 2º arquivo

### Requirement: Extração assíncrona no cluster pdf

A criação SHALL enfileirar o processamento do caso no cluster `pdf` (django-q2); o processamento SHALL extrair o texto de cada documento com PyMuPDF na ordem declarada, concatenando em `Case.extracted_text` (fonte única de texto), remover a marca d'água característica e extrair o número de ocorrência para `agency_record_number` quando presente. O processamento SHALL ainda extrair os metadados do cabeçalho padrão SESAB (repetido por página) quando presentes — idade (`Idade: N a.` na linha de demografia), sexo, raça/cor e dias em tela (maior ocorrência de `Dias em tela: N`, com o valor na mesma linha OU na linha imediatamente seguinte quando o rótulo está sozinho — layout real do cabeçalho) e a **unidade de origem** (`Unid. Origem:`, valor na mesma linha OU na linha imediatamente seguinte) — persistindo-os nos campos do caso (`patient_age`/`patient_gender`/`patient_race`/`days_on_screen`/`origin_unit`), sem carregar esses valores em eventos. Campos ausentes do cabeçalho ficam vazios no caso. O ciclo SHALL percorrer `NEW → PDF_EXTRACTING → ANONYMIZING` com eventos de ator sistema, operando sob lock do caso (contexto de worker). Falha de extração (documento ilegível/corrompido) SHALL levar o caso a `FAILED` com motivo na trilha.

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

O NIR SHALL conseguir revisar casos retidos pelo gate na tela de detalhe, com duas ações: **liberar** (bypass — o caso avança para `ANONYMIZING` com evento de bypass registrado) e **reenviar documento** (substitui o PDF do caso por exatamente 1 novo arquivo — erro nomeado caso contrário, sem efeito — e reprocessa do início da extração, zerando a flag e o texto anterior). Ambas as ações ficam disponíveis apenas enquanto o caso estiver retido.

#### Scenario: Liberar avança com evento de bypass

- **GIVEN** um caso retido em `PDF_EXTRACTING` com `manual_review_required=True`
- **WHEN** o NIR libera o caso
- **THEN** o caso avança para `ANONYMIZING` e a trilha registra o evento de bypass com o NIR como ator

#### Scenario: Reenviar documentos reprocessa do zero

- **GIVEN** um caso retido com documento inválido
- **WHEN** o NIR reenvia exatamente 1 PDF válido
- **THEN** o documento antigo é substituído, a flag de revisão é zerada, o texto anterior é descartado e o caso é reprocessado

#### Scenario: Reenvio do gate exige exatamente um PDF

- **GIVEN** um caso retido
- **WHEN** o NIR reenvia 0 ou mais de 1 arquivo
- **THEN** nada é alterado e o erro nomeado informa que o reenvio aceita exatamente 1 PDF

### Requirement: Meus casos e detalhe do NIR
O NIR SHALL ver, numa lista "meus casos", apenas os casos criados por ele, com status, tipos declarados, indicador de retenção pelo gate e a identificação do paciente (nome e idade quando presentes — o NIR é o criador do caso); o detalhe de um caso SHALL exibir a identificação do paciente (nome e idade, padrão da lista) num card «Identificação do paciente» com badge de status no header, além de documentos (com visualização do PDF) e comunicações — **sem a trilha de eventos** (a trilha vive no painel; caso `FAILED` exibe badge de erro «Falha no processamento»). Acesso a caso criado por outro usuário SHALL ser negado. Os cards NÃO exibem o uid completo do caso — a única exceção é o sufixo curto de desambiguação (`· <8 chars>`), exibido apenas no cenário de nº duplicado descrito abaixo. Toda referência textual a um caso no âmbito do NIR — `<title>`, breadcrumb, cabeçalho do detalhe, mensagens flash e alerta de lote — SHALL usar a **referência humana**: nº de ocorrência com fallback `uid <8 primeiros chars>` (padrão do painel); o uid completo NÃO é exibido em texto visível (permanece apenas como chave das URLs). A linha rotulada «Nº de ocorrência» dos cards mantém `—` quando o nº está ausente (decisão vigente de `queues-remove-uid`: card sem nº fica apenas com a identificação do paciente) — o fallback em `uid <8 chars>` aplica-se a referências (títulos, breadcrumbs, cabeçalho do detalhe, mensagens), não a essa linha rotulada. Onde dois casos compartilham o mesmo nº (reenvio corrigido cria caso novo), a referência SHALL ser desambiguada com sufixo curto (`nº · <8 chars>`): link de «Reenvios corrigidos» no detalhe, página «Reenviar corrigido», flash do novo caso e cards de «meus casos» quando a lista contém nº duplicado. O detalhe exibe TODAS as rows de procedimento do caso com a origem («Declarado» NIR / «Detectado na extração») e o status de detecção após a reconciliação; no card de revisão por divergência, o resumo declarados × detectados acompanha a ação de liberar.
 A lista segue o padrão ats-web: visual de cards com alerta de espera, abas `Ativos/Encerrados` com contadores, filtros server-side (busca por nº de ocorrência com mínimo de 3 caracteres, status, tipo de procedimento) e atualização automática via partial; escopo por criador e semântica das abas preservados.

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
- **THEN** vê os PDFs enviados (servidos de forma segura) e as comunicações do caso, sem a trilha de eventos

#### Scenario: Card de meus casos identifica o paciente

- **GIVEN** um caso criado pelo NIR com nome e idade extraídos do cabeçalho
- **WHEN** o NIR abre meus casos
- **THEN** o card exibe o nome do paciente e a idade (`84 a`); caso sem identificação exibe `—` no lugar do nome e omite a idade

#### Scenario: Caso FAILED exibe badge de erro no detalhe do NIR

- **GIVEN** um caso do próprio NIR em `FAILED`
- **WHEN** o NIR abre o detalhe
- **THEN** um badge de erro «Falha no processamento» é exibido, sem a trilha de eventos nem o motivo técnico

#### Scenario: Cards sem o identificador interno do caso

- **GIVEN** casos com uid interno e nº de ocorrência
- **WHEN** a lista é renderizada
- **THEN** nenhum card exibe o uid do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores do card

#### Scenario: Detalhe retido por divergência lista declarados e detectados

- **GIVEN** um caso do próprio NIR retido em `LLM_EXTRACTING` por divergência (`angio_art_perif` declarada não-detectada e `art_perif` detectada não-declarada)
- **WHEN** o NIR abre o detalhe
- **THEN** a seção de procedimentos exibe ambas as rows com os rótulos legíveis e os badges de origem e detecção («Não detectado», «Detectado na extração»), e o card de revisão resume declarados × detectados junto ao botão de liberação

#### Scenario: Detalhe do NIR identifica o paciente sem uid completo

- **GIVEN** um caso do próprio NIR com nome, idade e nº de ocorrência extraídos
- **WHEN** o NIR abre o detalhe
- **THEN** o card «Identificação do paciente» exibe nome, idade e nº de ocorrência com badge de status no header; `<title>`, breadcrumb e cabeçalho citam a referência humana («Caso <nº>») e o uid completo não aparece em texto visível (apenas em hrefs)

#### Scenario: Detalhe sem nº de ocorrência usa o fallback uid curto

- **GIVEN** um caso do próprio NIR sem nº de ocorrência (retido no gate)
- **WHEN** o NIR abre o detalhe
- **THEN** `<title>`, breadcrumb e cabeçalho exibem `uid <8 primeiros chars>` no lugar do nº, sem o uid completo

#### Scenario: Mensagens do fluxo citam a referência humana

- **GIVEN** um caso do próprio NIR com nº de ocorrência
- **WHEN** o NIR conclui criação, confirmação de recebimento, liberação de gate ou reenvio de documentos
- **THEN** a mensagem flash cita a referência humana (nº de ocorrência; fallback `uid <8 chars>` quando ausente), nunca o uid completo

#### Scenario: Referência do reenvio corrigido é desambiguada

- **GIVEN** um caso encerrado com nº N e um reenvio corrigido criado (novo caso com o mesmo nº N)
- **WHEN** o NIR vê o flash de reenvio criado, a página «Reenviar corrigido» do original, e o link do novo caso na lista «Reenvios corrigidos» do detalhe
- **THEN** a referência exibida é `N · <8 primeiros chars do uid>` para distinguir original e novo caso

#### Scenario: Meus casos com nº duplicado exibe sufixo curto

- **GIVEN** dois casos do mesmo NIR compartilhando o nº N (original encerrado + reenvio corrigido)
- **WHEN** o NIR abre «meus casos»
- **THEN** os cards dos casos com nº N exibem o nº acompanhado do sufixo curto (`N · <8 chars>`); cards com nº único permanecem apenas com o nº

#### Scenario: Filtros server-side compõem por AND

- **GIVEN** casos do NIR com nºs, status e tipos distintos
- **WHEN** filtra por nº (≥3 caracteres) + status + tipo de procedimento
- **THEN** apenas os casos que atendem a todos os critérios são listados, com os filtros preservados entre página cheia e partial

#### Scenario: Abas Ativos e Encerrados com contadores

- **GIVEN** 2 casos ativos e 1 encerrado (CLEANED) do NIR
- **WHEN** abre meus casos
- **THEN** as abas exibem as contagens 2 e 1, e cada aba lista só os casos da situação correspondente

#### Scenario: Partial atualiza meus casos sem reload

- **GIVEN** meus casos abertos e um novo caso criado pelo NIR, compatível com os filtros ativos
- **WHEN** decorre o intervalo de atualização
- **THEN** o novo card aparece sem recarregar a página, preservando aba e filtros

#### Scenario: Caso fora do filtro não aparece no refresh

- **GIVEN** meus casos filtrados por um status e um novo caso criado em outro status
- **WHEN** decorre o intervalo de atualização
- **THEN** nenhum card novo aparece e os filtros seguem aplicados
