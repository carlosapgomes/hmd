# Delta: scheduling

## MODIFIED Requirements

### Requirement: Fila do agendador completa

O sistema SHALL exibir ao papel ativo `scheduler`/`admin` as abas `Pendentes` (`SCHEDULER_REQUESTED`/`AWAITING_SCHEDULING`) e `Processados`, ordenadas por `days_on_screen` desc nulls-last e FIFO, paginadas e sem filtro de unidade. Pendentes terá filtro server-side opcional por tipo de procedimento, busca client-side e cards 8/12 + tempo/ação 4/12 em desktop, com espera única, decisões médicas por procedimento e ações de reserva. Guards, partial a cada 20 s, contadores, unidade exibida quando definida e apresentação da aba processada são preservados; uid nunca é exibido.

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

#### Scenario: Aba processada usa rótulo de recebimento

- **GIVEN** um caso já processado pelo agendamento com nome e idade
- **WHEN** o agendador acessa a aba processados
- **THEN** o card exibe o rótulo «Recebido há …» e NÃO exibe «Aguardando», com a idade junto do nome

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

- **GIVEN** 2 casos aguardando e 4 processados acessíveis ao agendador
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
