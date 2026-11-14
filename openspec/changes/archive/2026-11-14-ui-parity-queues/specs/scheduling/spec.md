# Delta: scheduling

## MODIFIED Requirements

### Requirement: Fila do agendador completa

O sistema SHALL exibir ao papel ativo `scheduler`/`admin` a fila de casos prontos para agendamento (estados `SCHEDULER_REQUESTED` e `AWAITING_SCHEDULING` — pedidos novos e casos reabertos por intercorrência), ordenada por **tempo de tela** — `days_on_screen` (descendente, casos sem o campo ao fim), desempate FIFO por `created_at` — paginada, **sem diferenciação por unidade**, com aba de casos processados do agendamento; os cards exibem nome e idade do paciente (quando presentes), nº de ocorrência, tempo de espera e o tempo de tela oficial quando presente. Antes de montar a aba aguardando, o sistema SHALL expirar reservas vencidas dos casos nesses estados; cada card dessa aba SHALL refletir o estado de reserva: sem reserva → ação livre que abre a página de confirmação; reservado pelo próprio agendador → ação «Continuar»; reservado por outro portador → botão desabilitado «Reservado» com o nome do portador. Outros papéis ativos recebem 403; anônimo é redirecionado ao login. Os cards NÃO exibem o identificador interno do caso (uid). A apresentação segue o padrão ats-web: abas `Pendentes/Processados` (rótulos já vigentes no `_nav` do change 1), visual `case-card` com alerta de espera, badges de contagem nas abas, busca client-side por nome/ocorrência e atualização automática via partial a cada 20 s; ordenação, guards e paginação server-side preservados.

#### Scenario: Agendador vê a fila completa

- **GIVEN** casos em `SCHEDULER_REQUESTED` de subtipos distintos e um caso reaberto por intercorrência em `AWAITING_SCHEDULING`
- **WHEN** o agendador acessa a fila
- **THEN** todos são listados na aba de aguardando, ordenados por tempo de tela (desempate FIFO), sem filtro de unidade

#### Scenario: Papel não agendador recebe 403

- **GIVEN** usuário com papel ativo `doctor`
- **WHEN** acessa a fila do agendador
- **THEN** recebe HTTP 403

#### Scenario: Card do agendador com identificação e tempo

- **GIVEN** um caso em `AWAITING_SCHEDULING` com nome, idade 84 e `days_on_screen` 6
- **WHEN** o agendador acessa a fila (aba aguardando)
- **THEN** o card exibe nome e idade (`84 a`), o rótulo «Aguardando há …» com o tempo desde o recebimento e o badge `6 d em tela`

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
- **THEN** nenhum card exibe o uid do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores do card

#### Scenario: Caso reservado por outro agendador aparece bloqueado

- **GIVEN** um caso pronto para agendamento com reserva ativa de outro agendador (unidade distinta)
- **WHEN** o agendador acessa a fila (aba aguardando)
- **THEN** o card exibe o botão «Reservado» desabilitado com o nome do portador, sem ação livre

#### Scenario: Continuar a própria reserva do agendador

- **GIVEN** um caso pronto para agendamento com reserva ativa do próprio agendador
- **WHEN** ele acessa a fila (aba aguardando)
- **THEN** o card exibe a ação «Continuar» que reabre a página de trabalho do agendamento

#### Scenario: Partial atualiza a fila sem reload

- **GIVEN** a fila aberta e um novo caso chegando à fila de agendamento, posicionado na página exibida
- **WHEN** decorre o intervalo de atualização
- **THEN** o novo card aparece sem recarregar a página, preservando aba e paginação

#### Scenario: Caso de outra página não aparece

- **GIVEN** a fila com paginação e um novo caso posicionado em outra página
- **WHEN** decorre o intervalo de atualização
- **THEN** nenhum card novo aparece na página exibida

#### Scenario: Busca client-side filtra os cards

- **GIVEN** a fila com vários casos renderizados
- **WHEN** o agendador digita o nome do paciente ou o nº de ocorrência na busca
- **THEN** apenas os cards correspondentes permanecem visíveis, sem nova requisição

#### Scenario: Badges de contagem nas abas

- **GIVEN** 2 casos aguardando e 4 processados acessíveis ao agendador
- **WHEN** a fila é renderizada
- **THEN** as abas exibem as contagens 2 e 4
