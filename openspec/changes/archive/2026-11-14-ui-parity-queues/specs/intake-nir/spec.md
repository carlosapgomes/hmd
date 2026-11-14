# Delta: intake-nir

## MODIFIED Requirements

### Requirement: Meus casos e detalhe do NIR

O NIR SHALL ver, numa lista "meus casos", apenas os casos criados por ele, com status, tipos declarados, indicador de retenção pelo gate e a identificação do paciente (nome e idade quando presentes — o NIR é o criador do caso); o detalhe de um caso SHALL exibir documentos (com visualização do PDF) e comunicações — **sem a trilha de eventos** (a trilha vive no painel; caso `FAILED` exibe badge de erro «Falha no processamento»). Acesso a caso criado por outro usuário SHALL ser negado. Os cards NÃO exibem o identificador interno do caso (uid). O detalhe exibe TODAS as rows de procedimento do caso com a origem («Declarado» NIR / «Detectado na extração») e o status de detecção após a reconciliação; no card de revisão por divergência, o resumo declarados × detectados acompanha a ação de liberar. A lista segue o padrão ats-web: visual de cards com alerta de espera, abas `Ativos/Encerrados` com contadores, filtros server-side (busca por nº de ocorrência com mínimo de 3 caracteres, status, tipo de procedimento) e atualização automática via partial; escopo por criador e semântica das abas preservados.

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
