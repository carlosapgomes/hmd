# Delta: doctor-decision

## MODIFIED Requirements

### Requirement: Fila médica com filtro por estado e subtipo

O sistema SHALL exibir a fila de casos para o papel ativo `doctor`/`admin`,
segmentada por estado (`aguardando decisão` = `AWAITING_DOCTOR`; `decididos` =
estados pós-decisão), ordenada por **tempo de tela** — `days_on_screen`
(descendente, casos sem o campo ao fim), desempate FIFO por `created_at` —
com filtro por subtipo de
procedimento: `Todas` + os subtipos atribuídos ao usuário (generalista sem
subtipo e `admin` veem `Todas` e qualquer subtipo). Os cards SHALL exibir a
identificação do paciente (nome e idade quando presentes), nº de ocorrência,
tempo de espera (desde `created_at`) e o tempo de tela oficial do cabeçalho
(dias em tela) quando presente. Antes de montar a aba aguardando, o sistema
SHALL expirar reservas vencidas dos casos em `AWAITING_DOCTOR`; cada card
dessa aba SHALL refletir o estado de reserva do caso: sem reserva → ação
«Avaliar» que abre a página de trabalho de decisão; reservado pelo próprio
médico → ação «Continuar avaliação»; reservado por outro portador → botão
desabilitado «Reservado» com o nome do portador. Outros papéis ativos
recebem 403. Os cards NÃO exibem o identificador interno do caso (uid). A
apresentação segue o padrão ats-web: abas `Pendentes/Decididos` (rótulo
`Decididos` fiel à query por estado, sem recorte de data), visual
`case-card` com alerta de espera, badges de contagem nas abas, busca
client-side por nome/ocorrência e atualização automática via partial a
cada 20 s; o filtro server-side por subtipo permanece a base (o filtro
client apenas refina o renderizado, nunca amplia o escopo).

#### Scenario: Médico de subtipo filtra a fila

- **GIVEN** um médico com subtipo `angio` e casos em `AWAITING_DOCTOR` de subtipos `angio` e `cardio`
- **WHEN** acessa a fila com o filtro `angio`
- **THEN** apenas casos com ao menos um tipo declarado de subtipo `angio` são listados

#### Scenario: Generalista vê qualquer subtipo

- **GIVEN** um médico sem subtipos atribuídos e casos em `AWAITING_DOCTOR` de subtipos distintos
- **WHEN** acessa a fila com o filtro `Todas`
- **THEN** casos de todos os subtipos são listados

#### Scenario: Papel não médico recebe 403

- **GIVEN** usuário com papel ativo `nir`
- **WHEN** acessa a fila médica
- **THEN** recebe HTTP 403 sem listagem

#### Scenario: Fila ordenada pelo tempo de tela

- **GIVEN** casos em `AWAITING_DOCTOR` com `days_on_screen` 10, 3 e um sem cabeçalho (upload mais antigo)
- **WHEN** o médico acessa a fila
- **THEN** a ordem é 10, 3 e o caso sem cabeçalho ao fim (desempate FIFO), independentemente do momento do upload

#### Scenario: Card com identificação e tempo de espera

- **GIVEN** um caso em `AWAITING_DOCTOR` com nome, idade 84 e `days_on_screen` 6
- **WHEN** o médico acessa a fila (aba aguardando)
- **THEN** o card exibe nome e idade (`84 a`), o rótulo «Aguardando há …» com o tempo desde o recebimento e o badge `6 d em tela`

#### Scenario: Aba decidida usa rótulo de recebimento

- **GIVEN** um caso decidido com nome e idade
- **WHEN** o médico acessa a aba decididos
- **THEN** o card exibe o rótulo «Recebido há …» (sem «Aguardando», que não faz sentido em histórico) e a idade junto do nome

#### Scenario: Cards sem o identificador interno do caso

- **GIVEN** casos com uid interno e nº de ocorrência
- **WHEN** a fila é renderizada
- **THEN** nenhum card exibe o uid do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores do card

#### Scenario: Caso reservado por outro médico aparece bloqueado

- **GIVEN** um caso em `AWAITING_DOCTOR` com reserva ativa de outro médico
- **WHEN** o médico acessa a fila (aba aguardando)
- **THEN** o card exibe o botão «Reservado» desabilitado com o nome do portador, sem ação primária

#### Scenario: Reserva do próprio médico oferece continuar

- **GIVEN** um caso em `AWAITING_DOCTOR` com reserva ativa do próprio médico
- **WHEN** ele acessa a fila (aba aguardando)
- **THEN** o card exibe a ação «Continuar avaliação» que reabre a página de trabalho

#### Scenario: Reserva vencida não bloqueia o card

- **GIVEN** um caso em `AWAITING_DOCTOR` com reserva cuja lease venceu
- **WHEN** o médico acessa a fila
- **THEN** a reserva vencida é expirada antes da montagem e o card exibe a ação «Avaliar» normalmente

#### Scenario: Abas usam Pendentes e Decididos

- **GIVEN** casos aguardando e decididos (de hoje e de dias anteriores)
- **WHEN** o médico abre a fila
- **THEN** as abas exibem `Pendentes` e `Decididos`, e a aba de decididos lista os casos nos estados da aba, independentemente da data da decisão (sem recorte de data)

#### Scenario: Partial atualiza a fila sem reload

- **GIVEN** a fila aberta e um novo caso chegando a `AWAITING_DOCTOR`, compatível com o subtipo ativo e posicionado na página exibida
- **WHEN** decorre o intervalo de atualização
- **THEN** o novo card aparece sem recarregar a página, preservando aba, filtro e paginação

#### Scenario: Caso fora do filtro ou da página não aparece

- **GIVEN** a fila filtrada por um subtipo e um novo caso de outro subtipo, ou um caso posicionado em outra página
- **WHEN** decorre o intervalo de atualização
- **THEN** nenhum card novo aparece na página exibida

#### Scenario: Busca client-side filtra os cards

- **GIVEN** a fila com vários casos renderizados
- **WHEN** o médico digita o nome do paciente ou o nº de ocorrência na busca
- **THEN** apenas os cards correspondentes permanecem visíveis, sem nova requisição

#### Scenario: Badges de contagem nas abas

- **GIVEN** 3 casos aguardando e 5 decididos acessíveis ao médico
- **WHEN** a fila é renderizada
- **THEN** as abas exibem as contagens 3 e 5
