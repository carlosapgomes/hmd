# Delta: intake-nir

## MODIFIED Requirements

### Requirement: Meus casos e detalhe do NIR

O NIR SHALL ver somente seus casos, com status, tipos, retenção e identificação; o detalhe preserva documentos/comunicações, sem trilha, e nega caso alheio. Meus casos terá página e lista na largura do header, navegação Ativos/Encerrados apenas no header, filtros server-side preservados, contagem neutra e cards compactos em quatro regiões no desktop (4/3/3/2), sem alerta de espera ou resumo redundante. O detalhe mantém origem/detecção das rows; uid nunca é exibido; partial, contadores e encerramento administrativo são preservados.

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

- **GIVEN** um caso criado pelo NIR com nome, idade, sexo e origem extraídos do cabeçalho
- **WHEN** o NIR abre meus casos
- **THEN** o card exibe nome, idade (`84 a`), ocorrência e demografia disponível; sem nome exibe `—` e omite idade

#### Scenario: Caso FAILED exibe badge de erro no detalhe do NIR

- **GIVEN** um caso do próprio NIR em `FAILED`
- **WHEN** o NIR abre o detalhe
- **THEN** um badge de erro «Falha no processamento» é exibido, sem a trilha de eventos nem o motivo técnico

#### Scenario: Cards sem o identificador interno do caso

- **GIVEN** casos com uid interno e nº de ocorrência
- **WHEN** a lista é renderizada
- **THEN** nenhum card exibe o uid do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores visíveis

#### Scenario: Detalhe retido por divergência lista declarados e detectados

- **GIVEN** um caso do próprio NIR retido em `LLM_EXTRACTING` por divergência (`angio_art_perif` declarada não-detectada e `art_perif` detectada não-declarada)
- **WHEN** o NIR abre o detalhe
- **THEN** a seção de procedimentos exibe ambas as rows com os rótulos legíveis e os badges de origem e detecção, e o card de revisão resume declarados × detectados junto ao botão de liberação

#### Scenario: Filtros server-side compõem por AND

- **GIVEN** casos do NIR com nºs, status e tipos distintos
- **WHEN** filtra por nº (≥3 caracteres) + status + tipo de procedimento
- **THEN** apenas os casos que atendem a todos os critérios são listados, com os filtros preservados entre página cheia e partial

#### Scenario: Abas Ativos e Encerrados com contadores

- **GIVEN** 2 casos ativos e 1 encerrado (`CLEANED`) do NIR
- **WHEN** abre meus casos
- **THEN** o header exibe Meus Casos/Casos Encerrados com as contagens 2 e 1, cada destino lista só a situação correspondente e nenhuma segunda pill Ativos/Encerrados aparece no corpo da página

#### Scenario: Troca de aba pelo header preserva filtros

- **GIVEN** meus casos com ocorrência, status e tipo filtrados
- **WHEN** o NIR alterna entre Meus Casos e Casos Encerrados pelo header
- **THEN** o parâmetro `tab` muda e os filtros ativos são preservados na URL e na listagem

#### Scenario: Partial atualiza meus casos sem reload

- **GIVEN** meus casos abertos e um novo caso criado pelo NIR, compatível com os filtros ativos
- **WHEN** decorre o intervalo de atualização
- **THEN** o novo card aparece sem recarregar a página, preservando aba e filtros

#### Scenario: Caso fora do filtro não aparece no refresh

- **GIVEN** meus casos filtrados por um status e um novo caso criado em outro status
- **WHEN** decorre o intervalo de atualização
- **THEN** nenhum card novo aparece e os filtros seguem aplicados

#### Scenario: Página e lista têm hierarquia neutra

- **GIVEN** meus casos com resultados
- **WHEN** a página é renderizada
- **THEN** o título é «Todos os seus encaminhamentos», a quantidade aparece em cabeçalho neutro da lista e não existe alerta amarelo de contagem nem CTA «Enviar relatório» duplicado no topo

#### Scenario: Card compacto tem quatro regiões no desktop

- **GIVEN** um caso com procedimento, recebimento, status e detalhe disponível
- **WHEN** o card é renderizado em desktop
- **THEN** identificação ocupa 4/12, recebimento/complementos 3/12, status/alertas 3/12 e «Ver detalhes» 2/12 à direita, numa única superfície, sem `summary-box`, `waiting-time` ou item de lista aninhado

#### Scenario: Status e exceções permanecem visíveis no card compacto

- **GIVEN** casos com andamento, falha, retenção e encerramento administrativo
- **WHEN** os cards são renderizados
- **THEN** cada um mantém status e badges semânticos correspondentes, e o encerramento administrativo conserva resultado e motivo sem recriar uma caixa de resumo

#### Scenario: Estado vazio orienta a próxima ação

- **GIVEN** uma listagem sem resultados
- **WHEN** há filtros ativos
- **THEN** o estado vazio centralizado oferece «Limpar filtros»; sem filtros, informa a ausência da situação da aba e oferece «Novo Encaminhamento» quando aplicável

#### Scenario: Card compacto permanece utilizável no mobile

- **GIVEN** um card com nome longo, múltiplos procedimentos e ação
- **WHEN** a lista é exibida em viewport de 390 px
- **THEN** as regiões empilham, badges quebram linha, «Ver detalhes» ocupa a largura disponível com alvo mínimo de 44 px e não há overflow horizontal
