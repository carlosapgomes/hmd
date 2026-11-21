# Spec Delta

## Purpose

N/A — capability existente (`intake-nir`); delta modifica requisito vigente.

## MODIFIED Requirements

### Requirement: Meus casos e detalhe do NIR

O NIR SHALL ver, numa lista "meus casos", apenas os casos criados por ele, com status, tipos declarados, indicador de retenção pelo gate e a identificação do paciente (nome e idade quando presentes — o NIR é o criador do caso); o detalhe de um caso SHALL exibir a identificação do paciente (nome e idade, padrão da lista) num card «Identificação do paciente» com badge de status no header, além de documentos (com visualização do PDF), comunicações e a **trilha de eventos** em card collapsible «Trilha de eventos» ao final do detalhe — a mesma trilha do painel (paridade total de auditoria): cada evento com rótulo legível, badge por categoria, ator com papel em português e data/hora, card fechado por padrão; caso `FAILED` mantém o badge de erro «Falha no processamento» e o motivo técnico permanece fora da exibição. Acesso a caso criado por outro usuário SHALL ser negado. Os cards NÃO exibem o uid completo do caso — a única exceção é o sufixo curto de desambiguação (`· <8 chars>`), exibido apenas no cenário de nº duplicado descrito abaixo. Toda referência textual a um caso no âmbito do NIR — `<title>`, breadcrumb, cabeçalho do detalhe, mensagens flash e alerta de lote — SHALL usar a **referência humana**: nº de ocorrência com fallback `uid <8 primeiros chars>` (padrão do painel); o uid completo NÃO é exibido em texto visível (permanece apenas como chave das URLs). A linha rotulada «Nº de ocorrência» dos cards mantém `—` quando o nº está ausente (decisão vigente de `queues-remove-uid`: card sem nº fica apenas com a identificação do paciente) — o fallback em `uid <8 chars>` aplica-se a referências (títulos, breadcrumbs, cabeçalho do detalhe, mensagens), não a essa linha rotulada. Onde dois casos compartilham o mesmo nº (reenvio corrigido cria caso novo), a referência SHALL ser desambiguada com sufixo curto (`nº · <8 chars>`): link de «Reenvios corrigidos» no detalhe, página «Reenviar corrigido», flash do novo caso e cards de «meus casos» quando a lista contém nº duplicado. O detalhe exibe TODAS as rows de procedimento do caso com a origem («Declarado» NIR / «Detectado na extração») e o status de detecção após a reconciliação; no card de revisão por divergência, o resumo declarados × detectados acompanha a confirmação explícita nos novos envios e a liberação no contrato histórico.

Meus casos terá página e lista na largura do header, navegação Ativos/Encerrados apenas no header, filtros server-side preservados, contagem neutra e cards compactos em quatro regiões no desktop (4/3/3/2), sem alerta de espera ou resumo redundante. O partial, os contadores e o encerramento administrativo são preservados.

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
- **THEN** vê os PDFs enviados (servidos de forma segura), as comunicações do caso e, ao final, o card «Trilha de eventos» fechado por padrão; ao abrir, cada evento exibe rótulo legível, ator com papel em português e data/hora

#### Scenario: Card de meus casos identifica o paciente
- **GIVEN** um caso criado pelo NIR com nome, idade, sexo e origem extraídos do cabeçalho
- **WHEN** o NIR abre meus casos
- **THEN** o card exibe nome, idade (`84 a`), ocorrência e demografia disponível; sem nome exibe `—` e omite idade

#### Scenario: Caso FAILED exibe badge de erro no detalhe do NIR
- **GIVEN** um caso do próprio NIR em `FAILED`
- **WHEN** o NIR abre o detalhe
- **THEN** um badge de erro «Falha no processamento» é exibido; a trilha ao final exibe o evento de falha com rótulo legível e ator, e o motivo técnico permanece fora da exibição

#### Scenario: Trilha do NIR mostra quem fez o quê
- **GIVEN** um caso do próprio NIR com decisão médica registrada por um médico e agendamento confirmado por um agendador
- **WHEN** o NIR abre o detalhe e expande a trilha de eventos
- **THEN** os eventos exibem o nome do ator com o papel em português e a data/hora de cada ação (decisão médica, agendamento), além dos eventos de sistema com ator «Sistema»

#### Scenario: Cards sem o identificador interno do caso
- **GIVEN** casos com uid interno e nº de ocorrência
- **WHEN** a lista é renderizada
- **THEN** nenhum card exibe o uid do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores visíveis

#### Scenario: Detalhe retido por divergência lista declarados e detectados
- **GIVEN** um caso novo do próprio NIR retido em `LLM_EXTRACTING` por divergência (`angio_art_perif` declarada não-detectada e `art_perif` detectada não-declarada)
- **WHEN** o NIR abre o detalhe
- **THEN** a seção de procedimentos exibe ambas as rows com rótulos legíveis e badges de origem/detecção e o card resume declarados × detectados junto ao formulário de confirmação, sem bypass simples

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
- **WHEN** o NIR conclui criação, confirmação de recebimento, liberação de gate, confirmação de procedimento ou reenvio de documentos
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
