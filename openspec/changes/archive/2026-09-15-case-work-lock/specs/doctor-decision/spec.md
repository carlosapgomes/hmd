# doctor-decision — delta case-work-lock

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
recebem 403. Os cards NÃO exibem o identificador interno do caso (uid).

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

### Requirement: Decisão por procedimento com motivo obrigatório em negativas

O sistema SHALL registrar a decisão médica por procedimento declarado
(`approved`/`denied`) com motivo obrigatório quando negado, validando no
formulário e persistindo rows, evento auditável e transição FSM no mesmo
atomic (todos negados → `DOCTOR_DENIED`; ao menos um aprovado →
`DOCTOR_ACCEPTED` com encadeamento para a fila de agendamento). A avaliação
acontece numa **página de trabalho única**: a leitura completa do caso
(identificação, procedimentos, sumário, estrutura, prior-case, documentos,
anexos, requisitos, recomendação agregada e alertas consultivos) precede o
formulário de decisão como último bloco da mesma página, com âncora
«Ir para o formulário de decisão» no topo e as ações [Registrar decisão] e
[Sair sem decidir] juntas ao fim do formulário. A submissão SHALL exigir
reserva vigente do caso (token de lock emitido na abertura) e, ao concluir
com sucesso, liberar a reserva. Submissão concorrente ou fora de
`AWAITING_DOCTOR` SHALL falhar sem efeito parcial, com mensagem clara ao
usuário.

#### Scenario: Negação exige motivo

- **GIVEN** o formulário com um procedimento marcado `denied` e motivo em branco
- **WHEN** o médico envia
- **THEN** o formulário rejeita com erro de validação e nada é persistido

#### Scenario: Decisão mista leva o caso ao agendamento

- **GIVEN** um caso com dois procedimentos declarados em `AWAITING_DOCTOR`
- **WHEN** o médico aprova um e nega o outro com motivo
- **THEN** as rows registram as disposições, o evento resume as decisões e o caso transita para a fila de agendamento

#### Scenario: Dupla submissão falha sem efeito parcial

- **GIVEN** um caso que acabou de ser decidido por outro médico
- **WHEN** uma segunda submissão chega para o mesmo caso
- **THEN** ela falha com mensagem de estado e nenhuma decisão adicional é gravada

#### Scenario: Avaliação e decisão num fluxo único

- **GIVEN** um caso em `AWAITING_DOCTOR`
- **WHEN** o médico abre a página de trabalho a partir da fila
- **THEN** a página apresenta a leitura completa e, ao fim, o formulário de decisão; a âncora «Ir para o formulário de decisão» existe no topo e o bloco «Decisões registradas» (histórico) não é renderizado nesta página

#### Scenario: Submissão sem reserva vigente falha

- **GIVEN** um caso em `AWAITING_DOCTOR` cujo token de reserva está ausente, inválido ou com lease vencida
- **WHEN** o médico envia o formulário de decisão
- **THEN** o formulário é reexibido com mensagem de reserva expirada e nada é persistido

#### Scenario: Decidir libera a reserva do caso

- **GIVEN** um caso em `AWAITING_DOCTOR` reservado pelo médico que decide
- **WHEN** a decisão é registrada com sucesso
- **THEN** a reserva é liberada e o caso segue o fluxo pós-decisão imediatamente disponível às filas seguintes

## ADDED Requirements

### Requirement: Reserva de caso durante a avaliação médica

O sistema SHALL reservar o caso para o médico no momento em que ele abre a
página de trabalho de decisão (`AWAITING_DOCTOR`): claim atômico com
contexto próprio e lease longa (renovável), gravando evento auditável na
trilha. Reserva ativa de outro portador SHALL barrar a abertura com mensagem
identificando o portador e retorno à fila — nunca erro 500. A reserva SHALL
ser renovada automaticamente enquanto houver atividade na página (heartbeat
com grace de inatividade) e SHALL ser liberada imediatamente quando o médico
sai sem decidir ou quando a decisão é registrada; sem renovação, a reserva
SHALL expirar sozinha e devolver o caso à fila. O mesmo médico reabrindo o
caso renova a reserva com novo token.

#### Scenario: Abrir a avaliação reserva o caso

- **GIVEN** um caso em `AWAITING_DOCTOR` sem reserva
- **WHEN** o médico abre a página de trabalho
- **THEN** o caso fica reservado para ele (portador registrado, lease no futuro, evento de claim na trilha) e o formulário carrega o token da reserva

#### Scenario: Outro médico é barrado pela reserva ativa

- **GIVEN** um caso em `AWAITING_DOCTOR` reservado pelo médico A
- **WHEN** o médico B tenta abrir a página de trabalho
- **THEN** ele recebe mensagem de que o caso está reservado (com o portador) e é redirecionado à fila, sem alterar a reserva

#### Scenario: Sair sem decidir libera imediatamente

- **GIVEN** um caso em `AWAITING_DOCTOR` reservado pelo médico
- **WHEN** ele aciona «Sair sem decidir»
- **THEN** a reserva é liberada na hora (evento de release na trilha) e ele volta à fila com o caso disponível

#### Scenario: Inatividade expira a reserva

- **GIVEN** um caso em `AWAITING_DOCTOR` com reserva cuja lease venceu sem renovação
- **WHEN** qualquer médico acessa a fila
- **THEN** a reserva vencida é expirada e o caso volta a exibir a ação livre

#### Scenario: Reabrir o próprio caso renova a reserva

- **GIVEN** um caso em `AWAITING_DOCTOR` reservado pelo médico A
- **WHEN** o médico A reabre a página de trabalho
- **THEN** a reserva é renovada com novo token e nova lease, sem barrar o acesso
