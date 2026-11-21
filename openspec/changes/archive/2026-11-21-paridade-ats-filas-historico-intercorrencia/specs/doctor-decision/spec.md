# Spec Delta

## MODIFIED Requirements

### Requirement: Fila médica operacional com filtro por estado e subtipo

O sistema SHALL exibir ao papel ativo `doctor`/`admin` as abas `Pendentes` (`AWAITING_DOCTOR`) e `Decididos Hoje` — casos com decisão médica registrada no dia corrente pelo próprio médico, independentemente do status atual do caso (paridade ats-web). Pendentes permanecem ordenadas por `days_on_screen` desc nulls-last e FIFO, com filtro server-side único por subtipo e busca client-side por nome/ocorrência; Decididos Hoje SHALL ser ordenada pela data/hora da decisão, mais recente primeira. Em desktop, cada card SHALL separar conteúdo (8/12) e tempo/ação (4/12). Pendentes exibem espera uma única vez e ações de reserva; decididos exibem disposição e data/hora por procedimento e «Ver detalhes», sem espera. Guards, paginação, partial a cada 20 s e contadores são preservados; uid nunca é exibido. Casos decididos em dias anteriores NÃO aparecem nas abas — paridade ats-web: o médico NÃO possui superfície de histórico própria (perda operacional aprovada pelo dono, registrada no design da change); o caso segue visível ao agendador e ao NIR pelas suas superfícies.

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

#### Scenario: Card pendente com identificação e tempo de espera únicos

- **GIVEN** um caso em `AWAITING_DOCTOR` com nome, idade 84, sexo, origem e `days_on_screen` 6
- **WHEN** o médico acessa a aba aguardando
- **THEN** o card exibe nome (`84 a`), ocorrência e demografia disponível, o badge «Dias em tela: 6» e exatamente um rótulo «Aguardando há …», sem repetir a data de recebimento

#### Scenario: Card decidido orientado ao resultado

- **GIVEN** um caso decidido hoje com dois procedimentos, disposições e horários persistidos
- **WHEN** o médico acessa a aba decididos
- **THEN** o card exibe nome/idade, ocorrência, disposição e data/hora de cada procedimento e o botão «Ver detalhes», sem «Aguardando há», «Recebido há» ou `waiting-time`

#### Scenario: Decididos Hoje limita-se às decisões do dia corrente

- **GIVEN** um caso decidido hoje pelo médico autenticado e outro decidido ontem ainda aguardando agendamento
- **WHEN** o médico acessa a aba decididos
- **THEN** apenas o caso decidido hoje aparece, ordenado pela data/hora da decisão (mais recente primeiro); o caso de ontem não aparece em nenhuma aba e segue visível ao agendador

#### Scenario: Decididos Hoje inclui apenas as próprias decisões

- **GIVEN** decisões médicas registradas hoje por outro médico
- **WHEN** o médico autenticado acessa a aba decididos
- **THEN** nenhuma delas é listada — a aba é escopada ao decisor

#### Scenario: Caso decidido hoje aparece mesmo já encerrado

- **GIVEN** um caso decidido hoje pelo médico que já completou o ciclo no mesmo dia (resposta final publicada, ciência registrada)
- **WHEN** o médico acessa a aba decididos
- **THEN** o caso permanece listado — o critério é a decisão do dia, independentemente do status atual

#### Scenario: Decisões mistas não são agregadas visualmente

- **GIVEN** um caso decidido com um procedimento aprovado e outro negado
- **WHEN** o card da aba decididos é renderizado
- **THEN** cada procedimento mantém seu próprio badge de disposição, aprovado em verde e negado em vermelho, sem um resultado único para o caso

#### Scenario: Cards sem o identificador interno do caso

- **GIVEN** casos com uid interno e nº de ocorrência
- **WHEN** a fila é renderizada
- **THEN** nenhum card exibe o uid do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores visíveis

#### Scenario: Caso reservado por outro médico aparece bloqueado

- **GIVEN** um caso em `AWAITING_DOCTOR` com reserva ativa de outro médico
- **WHEN** o médico acessa a aba aguardando
- **THEN** o card exibe «Reservado» desabilitado com o nome do portador, sem ação primária ou link de abertura

#### Scenario: Reserva do próprio médico oferece continuar

- **GIVEN** um caso em `AWAITING_DOCTOR` com reserva ativa do próprio médico
- **WHEN** ele acessa a aba aguardando
- **THEN** o card exibe a ação «Continuar avaliação» que reabre a página de trabalho

#### Scenario: Reserva vencida não bloqueia o card

- **GIVEN** um caso em `AWAITING_DOCTOR` com reserva cuja lease venceu
- **WHEN** o médico acessa a fila
- **THEN** a reserva vencida é expirada antes da montagem e o card exibe a ação «Avaliar» normalmente

#### Scenario: Abas usam Pendentes e Decididos

- **GIVEN** casos aguardando e casos decididos hoje pelo médico
- **WHEN** o médico abre a fila
- **THEN** as abas exibem `Pendentes` e `Decididos Hoje`, e decididos lista as decisões do dia corrente do próprio médico, independentemente do status atual do caso

#### Scenario: Título e container acompanham a aba

- **GIVEN** a fila médica aberta em qualquer aba
- **WHEN** a página é renderizada em desktop
- **THEN** o título é «Casos aguardando decisão» em pendentes ou «Casos decididos hoje» em decididos, e título, filtros, alertas e cards ocupam a mesma largura do container do header, sem margem superior extra

#### Scenario: Card operacional tem duas regiões no desktop

- **GIVEN** ao menos um card na fila em viewport desktop
- **WHEN** o card é renderizado
- **THEN** identificação e resumo ocupam a região principal de 8/12 e tempo/resultado/ação ocupam 4/12 à direita, dentro de uma única superfície de card, sem item de lista aninhado

#### Scenario: Filtro de subtipo tem um único controle

- **GIVEN** a fila com subtipos disponíveis e contagens de pendentes
- **WHEN** a aba aguardando é renderizada
- **THEN** existe apenas um seletor de subtipo, submetido ao server, com as contagens de pendentes em suas opções; nenhum segundo seletor client-side de exame/subtipo é exibido

#### Scenario: Decididos não exibe contadores de pendentes no filtro

- **GIVEN** a aba decididos aberta
- **WHEN** o filtro de subtipo é exibido
- **THEN** suas opções não anunciam contagens de pendentes e nenhuma linha «Pendentes por subtipo» aparece

#### Scenario: Partial atualiza a fila sem reload

- **GIVEN** a fila aberta e um novo caso chegando a `AWAITING_DOCTOR`, compatível com o subtipo ativo e posicionado na página exibida
- **WHEN** decorre o intervalo de atualização
- **THEN** o novo card aparece sem recarregar a página, preservando aba, filtro, busca e paginação

#### Scenario: Caso fora do filtro ou da página não aparece

- **GIVEN** a fila filtrada por um subtipo e um novo caso de outro subtipo, ou um caso posicionado em outra página
- **WHEN** decorre o intervalo de atualização
- **THEN** nenhum card novo aparece na página exibida

#### Scenario: Busca client-side filtra os cards

- **GIVEN** a fila com vários casos renderizados
- **WHEN** o médico digita o nome do paciente ou o nº de ocorrência na busca
- **THEN** apenas os cards correspondentes permanecem visíveis, sem nova requisição e sem alterar o escopo do filtro server-side

#### Scenario: Badges de contagem nas abas

- **GIVEN** 3 casos aguardando e 5 decisões do dia corrente pelo próprio médico
- **WHEN** a fila é renderizada
- **THEN** as abas exibem as contagens 3 e 5

#### Scenario: Estado vazio é informativo

- **GIVEN** uma aba sem casos no escopo atual
- **WHEN** ela é renderizada
- **THEN** um alerta informativo informa que não há casos aguardando avaliação ou que não há casos decididos hoje, sem um segundo card vazio redundante

#### Scenario: Layout permanece utilizável no mobile

- **GIVEN** um card com nome longo, múltiplos procedimentos e ação
- **WHEN** a fila é exibida em viewport de 390 px
- **THEN** as regiões empilham, badges quebram linha, a ação permanece acessível com alvo mínimo de 44 px e não há overflow horizontal
