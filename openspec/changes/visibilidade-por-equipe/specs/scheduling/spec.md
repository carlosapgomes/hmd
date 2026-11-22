# Specification (delta)

## Purpose

Fila do agendador multi-unidade — a aba «Processados Hoje» passa a refletir a equipe de agendamento (qualquer decisor), com decisor visível no card.

## MODIFIED Requirements

### Requirement: Fila do agendador completa

O sistema SHALL exibir ao papel ativo `scheduler`/`admin` as abas `Pendentes` (`SCHEDULER_REQUESTED`/`AWAITING_SCHEDULING`) e `Processados Hoje` — decisões de agendamento (confirmação ou negação) registradas no dia corrente **por qualquer agendador (equipe), independentemente do status atual do caso e de quem processou**. Pendentes permanecem ordenadas por `days_on_screen` desc nulls-last e FIFO; Processados Hoje SHALL ser ordenada pela data/hora da decisão de agendamento, mais recente primeiro, paginada, sem filtro de unidade e exibindo o decisor (nome legível) no card. Pendentes terá filtro server-side opcional por tipo de procedimento, busca client-side e cards 8/12 + tempo/ação 4/12 em desktop, com espera única, decisões médicas por procedimento e ações de reserva. Guards, partial a cada 20 s, contadores, unidade exibida quando definida e apresentação da aba processada são preservados; uid nunca é exibido. Casos decididos em dias anteriores NÃO aparecem nas abas — a consulta de casos antigos é atendida pelo «Histórico do agendador».

#### Scenario: Agendador vê a fila completa

- **GIVEN** casos em `SCHEDULER_REQUESTED` de subtipos distintos e um caso reaberto por intercorrência em `AWAITING_SCHEDULING`
- **WHEN** o agendador acessa a fila
- **THEN** todos são listados na aba de aguardando, ordenados por tempo de tela (desempate FIFO), sem filtro de unidade

#### Scenario: Papel não agendador recebe 403

- **GIVEN** usuário com papel ativo `doctor`
- **WHEN** acessa a fila do agendador
- **THEN** recebe HTTP 403

#### Scenario: Card do agendador com identificação e tempo

- **GIVEN** um caso em `AWAITING_SCHEDULING` com nome, idade 84, sexo, origem e `days_on_screen` 6
- **WHEN** o agendador acessa a aba aguardando
- **THEN** o card exibe nome (`84 a`), ocorrência e demografia disponível, o badge «Dias em tela: 6» e exatamente um rótulo «Aguardando há …», sem repetir a data de recebimento

#### Scenario: Fila do agendador ordenada pelo tempo de tela

- **GIVEN** casos aguardando com `days_on_screen` 10, 3 e um sem cabeçalho (upload mais antigo)
- **WHEN** o agendador acessa a fila
- **THEN** a ordem é 10, 3 e o caso sem cabeçalho ao fim (desempate FIFO), independentemente do momento do upload

#### Scenario: Processados Hoje limita-se às decisões do dia corrente

- **GIVEN** um caso com decisão de agendamento hoje e outro caso decidido ontem ainda não encerrado
- **WHEN** o agendador acessa a aba processados
- **THEN** apenas o caso decidido hoje aparece, ordenado pela data/hora da decisão (mais recente primeiro); o caso de ontem não aparece em nenhuma aba e permanece consultável pelo «Histórico do agendador»

#### Scenario: Processados Hoje inclui apenas as próprias decisões

- **GIVEN** decisões de agendamento registradas hoje por outro agendador
- **WHEN** o agendador autenticado acessa a aba processados
- **THEN** elas são listadas com o decisor visível no card — a aba é da equipe de agendamento, não do decisor

#### Scenario: Aba processada usa rótulo de recebimento

- **GIVEN** um caso já processado pelo agendamento com nome e idade
- **WHEN** o agendador acessa a aba processados
- **THEN** o card exibe o rótulo «Recebido há …», o decisor da decisão e NÃO exibe «Aguardando», com a idade junto do nome

#### Scenario: Cards sem o identificador interno do caso

- **GIVEN** casos com uid interno e nº de ocorrência
- **WHEN** a fila é renderizada
- **THEN** nenhum card exibe o uid do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores visíveis

#### Scenario: Card pendente mostra decisões por procedimento

- **GIVEN** um caso pronto para agendamento com um procedimento aprovado e outro negado
- **WHEN** o agendador acessa a aba aguardando
- **THEN** o resumo do card exibe cada procedimento, seu subtipo e sua disposição médica, sem agregar as decisões em um resultado único

#### Scenario: Caso reservado por outro agendador aparece bloqueado

- **GIVEN** um caso pronto para agendamento com reserva ativa de outro agendador (unidade distinta)
- **WHEN** o agendador acessa a aba aguardando
- **THEN** o card exibe «Reservado» desabilitado com o nome do portador, sem ação livre ou link de abertura

#### Scenario: Continuar a própria reserva do agendador

- **GIVEN** um caso pronto para agendamento com reserva ativa do próprio agendador
- **WHEN** ele acessa a aba aguardando
- **THEN** o card exibe a ação «Continuar» que reabre a página de trabalho do agendamento

#### Scenario: Caso livre oferece agendar

- **GIVEN** um caso pronto para agendamento sem reserva
- **WHEN** o agendador acessa a aba aguardando
- **THEN** o card exibe o botão primário «Agendar», alinhado à região de ação, que abre a página de trabalho

#### Scenario: Filtro por tipo de procedimento é server-side

- **GIVEN** casos pendentes com tipos declarados distintos
- **WHEN** o agendador seleciona um tipo de procedimento
- **THEN** somente casos com esse tipo declarado são listados, antes da paginação, sem restringir a fila por unidade

#### Scenario: Filtro de procedimento inválido é ignorado

- **GIVEN** a aba aguardando
- **WHEN** `procedure_type` contém valor fora do catálogo
- **THEN** o valor é ignorado e a fila completa é exibida, sem erro e sem ampliar o escopo para outras abas

#### Scenario: Partial atualiza a fila sem reload

- **GIVEN** a fila aberta e um novo caso chegando à fila de agendamento, compatível com o tipo ativo e posicionado na página exibida
- **WHEN** decorre o intervalo de atualização
- **THEN** o novo card aparece sem recarregar a página, preservando aba, tipo de procedimento, busca e paginação

#### Scenario: Caso de outra página não aparece

- **GIVEN** a fila com filtro de procedimento e um novo caso de outro tipo, ou um caso posicionado em outra página
- **WHEN** decorre o intervalo de atualização
- **THEN** nenhum card novo aparece na página exibida

#### Scenario: Busca client-side filtra os cards

- **GIVEN** a fila com vários casos renderizados
- **WHEN** o agendador digita o nome do paciente ou o nº de ocorrência na busca
- **THEN** apenas os cards correspondentes permanecem visíveis, sem nova requisição e sem alterar o escopo server-side

#### Scenario: Badges de contagem nas abas

- **GIVEN** 2 casos aguardando e 4 decisões de agendamento do dia corrente (de qualquer agendador)
- **WHEN** a fila é renderizada
- **THEN** as abas exibem as contagens 2 e 4

#### Scenario: Título, alerta e card seguem o modelo operacional

- **GIVEN** a aba aguardando com casos
- **WHEN** ela é renderizada em desktop
- **THEN** o título é «Solicitações de agendamento aguardando confirmação», a contagem usa alerta informativo, e cada card apresenta conteúdo em 8/12 e tempo/ação em 4/12 à direita, numa única superfície e na largura do container do header

#### Scenario: Estado vazio pendente é informativo

- **GIVEN** a aba aguardando sem casos no escopo atual
- **WHEN** ela é renderizada
- **THEN** um alerta informativo informa que nenhuma solicitação aguarda agendamento, sem alerta de warning nem um segundo card vazio

#### Scenario: Layout permanece utilizável no mobile

- **GIVEN** um card com múltiplos procedimentos, decisões e ação
- **WHEN** a fila é exibida em viewport de 390 px
- **THEN** as regiões empilham, badges quebram linha, a ação permanece acessível com alvo mínimo de 44 px e não há overflow horizontal

### Requirement: Visão do agendador limitada ao necessário

O detalhe do caso para o agendador SHALL exibir a identificação do paciente (nome, data de nascimento, número de ocorrência), o **sumário clínico completo re-identificado apenas na renderização** (paridade ats-web — o truncamento à primeira linha é removido), os procedimentos com as decisões médicas (incluindo motivos das negativas), os dados de agendamento, a thread de comunicações e, ao final, a trilha de eventos em card collapsible «Trilha de eventos» (a mesma do painel: rótulos legíveis com badge por categoria, ator com papel em português, data/hora; fechado por padrão) — sem os demais artefatos do pipeline (estrutura extraída, alertas da policy, sugestão). O `<title>` e o breadcrumb do detalhe SHALL usar a **referência humana** do caso — nº de ocorrência com fallback `uid <8 primeiros chars>` (padrão do painel) — e o uid completo NÃO aparece em texto visível (permanece apenas como chave das URLs). O PDF do relatório original e o texto extraído SHALL ficar disponíveis ao agendador somente após a decisão de agendamento — **para qualquer agendador da equipe, em casos com decisão registrada por qualquer decisor (decisão do dono 2026-11-22)** — por documento do relatório (o relatório pode chegar em múltiplos PDFs ordenados) — nos cards `Texto Extraído do PDF` e `Visualizar PDF Original` (spec `pdf-original-viewer`).

#### Scenario: Agendador vê o diagnóstico resumido re-identificado

- **GIVEN** um caso com `summary_text` de múltiplas linhas contendo pseudônimos e o mapa de pseudônimos do caso
- **WHEN** o agendador abre o detalhe do agendamento
- **THEN** o diagnóstico é exibido pelo sumário completo, já re-identificado (sem tokens), preservando as linhas do texto

#### Scenario: Agendador não vê artefatos clínicos além do permitido

- **GIVEN** um caso com sumário completo, estrutura extraída e alertas da policy persistidos
- **WHEN** o agendador abre o detalhe do agendamento
- **THEN** identificação, sumário completo e decisões médicas são exibidos e nenhum conteúdo de estrutura extraída, alerta da policy ou sugestão aparece na página

#### Scenario: PDF disponível apenas de caso processado pelo próprio agendador

- **GIVEN** um caso confirmado por qualquer agendador, com dois documentos de relatório
- **WHEN** qualquer agendador solicita o PDF de cada documento do caso
- **THEN** cada documento original é servido como PDF

#### Scenario: PDF negado a outro agendador ou a caso reaberto

- **GIVEN** um caso reaberto por intercorrência (fora dos estados pós-decisão de agendamento)
- **WHEN** qualquer agendador solicita o PDF
- **THEN** recebe HTTP 404 e nenhum conteúdo do documento é exposto

#### Scenario: Detalhe do agendador usa a referência humana

- **GIVEN** um caso pronto para agendamento com nº de ocorrência
- **WHEN** o agendador abre o detalhe
- **THEN** `<title>` e breadcrumb exibem «Caso <nº>» (`uid <8 chars>` quando o nº está ausente) e o uid completo não aparece em texto visível — apenas nos hrefs

#### Scenario: Texto extraído sob o mesmo gate do PDF

- **GIVEN** um caso confirmado por qualquer agendador, com texto extraído
- **WHEN** ele abre o detalhe do agendamento
- **THEN** o card `Texto Extraído do PDF` exibe o texto; fora do gate de estado, nem o card de texto nem o de PDF aparecem

#### Scenario: Trilha do agendador mostra quem decidiu e quem criou

- **GIVEN** um caso pronto para agendamento, criado por um NIR, com decisão médica registrada por um médico
- **WHEN** o agendador abre o detalhe e expande a trilha de eventos
- **THEN** os eventos de criação e de decisão médica exibem o nome do ator com o papel em português e a data/hora; o card permanece fechado por padrão e o sumário completo permanece visível acima

#### Scenario: PDF e texto de caso processado por outro agendador da equipe

- **GIVEN** um caso confirmado por outro agendador, em estado pós-decisão
- **WHEN** um agendador diferente abre o detalhe do agendamento
- **THEN** os cards `Texto Extraído do PDF` e `Visualizar PDF Original` aparecem e o PDF é servido normalmente (equipe de agendamento)

### Requirement: Histórico do agendador

O sistema SHALL oferecer ao papel ativo `scheduler`/`admin` uma página de histórico com
busca server-side por nº de ocorrência OU nome do paciente sobre os casos que passaram
por decisão de agendamento (confirmação, negação ou cancelamento por intercorrência),
incluindo casos encerrados (`CLEANED`) e casos com intercorrência ativa, de qualquer
médico e agendador — com no máximo 50 resultados por consulta, estado vazio sem critério
e link para o detalhe do caso no escopo histórico (leitura, sem ações de agendamento).
A busca SHALL usar a referência humana nos resultados e o uid completo NÃO aparece em
texto visível.

#### Scenario: Busca por nome encontra caso antigo encerrado

- **GIVEN** um caso `CLEANED` decidido há vários dias, cujo nome do paciente contém o termo buscado
- **WHEN** o agendador busca pelo nome no histórico
- **THEN** o caso é listado com referência humana, status legível e dados do agendamento anterior, mesmo não estando mais em nenhuma aba da fila

#### Scenario: Busca por número de ocorrência

- **GIVEN** casos com nº de ocorrência distinto
- **WHEN** o agendador busca pelo nº
- **THEN** apenas os casos correspondentes são listados, incluindo casos de outros agendadores

#### Scenario: Sem critério mantém estado vazio

- **GIVEN** a página de histórico aberta sem termo de busca
- **WHEN** renderizada
- **THEN** nenhum resultado é exibido e um estado vazio orienta o uso da busca

#### Scenario: Detalhe histórico sem ações de agendamento

- **GIVEN** um resultado de busca no histórico
- **WHEN** o agendador abre o detalhe do caso
- **THEN** o detalhe é exibido em leitura (mesmo conteúdo do detalhe do agendador) sem botões de confirmação, negação ou desmarcação, e o PDF/texto extraído seguem o gate de equipe — estados pós-decisão de agendamento com decisão registrada por qualquer decisor (decisão do dono 2026-11-22)

#### Scenario: Negativa médica encerrada não entra no histórico

- **GIVEN** um caso `CLEANED` que foi negado pelo médico sem nunca passar por decisão de agendamento
- **WHEN** o agendador busca pelo nome ou nº dele no histórico
- **THEN** o caso não é listado — o universo do histórico exige decisão de agendamento registrada

#### Scenario: Papel não agendador recebe 403

- **GIVEN** usuário com papel ativo `nir`
- **WHEN** acessa a página de histórico do agendador
- **THEN** recebe HTTP 403
