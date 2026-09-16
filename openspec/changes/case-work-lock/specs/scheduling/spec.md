# scheduling — delta case-work-lock

## MODIFIED Requirements

### Requirement: Fila do agendador completa

O sistema SHALL exibir ao papel ativo `scheduler`/`admin` a fila de casos prontos para agendamento (estados `SCHEDULER_REQUESTED` e `AWAITING_SCHEDULING` — pedidos novos e casos reabertos por intercorrência), ordenada por **tempo de tela** — `days_on_screen` (descendente, casos sem o campo ao fim), desempate FIFO por `created_at` — paginada, **sem diferenciação por unidade**, com aba de casos processados do agendamento; os cards exibem nome e idade do paciente (quando presentes), nº de ocorrência, tempo de espera e o tempo de tela oficial quando presente. Antes de montar a aba aguardando, o sistema SHALL expirar reservas vencidas dos casos nesses estados; cada card dessa aba SHALL refletir o estado de reserva: sem reserva → ação livre que abre a página de confirmação; reservado pelo próprio agendador → ação «Continuar»; reservado por outro portador → botão desabilitado «Reservado» com o nome do portador. Outros papéis ativos recebem 403; anônimo é redirecionado ao login. Os cards NÃO exibem o identificador interno do caso (uid).

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

### Requirement: Confirmação de agendamento com unidade e resposta final

O sistema SHALL confirmar o agendamento de um caso em `SCHEDULER_REQUESTED` ou `AWAITING_SCHEDULING` registrando unidade (1 ou 2), data/hora e local, encadeando as transições até `FINAL_REPLY_POSTED` no mesmo atomic (na origem `AWAITING_SCHEDULING` a transição de entrada de fila não é re-disparada) e postando na thread do caso a resposta final ao NIR: para unidade 1, o texto padrão com local e data/hora; para unidade 2, exatamente "Recusar o relatório — caso agendado na {label da unidade 2 configurada}, que comunicará a Secretaria" (com os defaults de ambiente: "Recusar o relatório — caso agendado na Unidade 2, que comunicará a Secretaria"). Data/hora no passado é rejeitada. A abertura da página de confirmação reserva o caso (contexto próprio do agendador) e a submissão exige reserva vigente (token de lock), liberando-a ao concluir com sucesso.

#### Scenario: Confirmação na unidade 1 publica resposta com data

- **GIVEN** um caso em `SCHEDULER_REQUESTED`
- **WHEN** o agendador confirma com unidade 1, data/hora futura e local
- **THEN** o caso chega a `FINAL_REPLY_POSTED` com os campos de agendamento persistidos e a thread do caso contém a resposta final com local e data/hora

#### Scenario: Confirmação na unidade 2 publica recusa ao relatório

- **GIVEN** um caso em `SCHEDULER_REQUESTED`
- **WHEN** o agendador confirma com unidade 2
- **THEN** a resposta final postada é exatamente o texto de recusa com encaminhamento à unidade 2 configurada/Secretaria

#### Scenario: Data no passado é rejeitada

- **GIVEN** um caso em `SCHEDULER_REQUESTED`
- **WHEN** a confirmação usa data/hora no passado
- **THEN** a submissão é rejeitada com erro de validação e nada é persistido

#### Scenario: Confirmação sem reserva vigente falha

- **GIVEN** um caso em `SCHEDULER_REQUESTED` cujo token de reserva está ausente, inválido ou expirado
- **WHEN** o agendador envia a confirmação
- **THEN** o formulário é reexibido com mensagem de reserva expirada e nada é persistido

## ADDED Requirements

### Requirement: Reserva de caso durante o agendamento

O sistema SHALL reservar o caso para o agendador quando ele abre a página de confirmação ou de negação (`SCHEDULER_REQUESTED`/`AWAITING_SCHEDULING`): claim atômico com contexto próprio e lease renovável por heartbeat enquanto há atividade. Reserva ativa de outro portador SHALL barrar a abertura com mensagem identificando o portador e retorno à fila; a reserva SHALL ser liberada imediatamente ao sair sem decidir ou ao concluir a confirmação/negação, e SHALL expirar sozinha sem renovação. O mesmo agendador reabrindo renova a reserva com novo token.

#### Scenario: Abrir a confirmação reserva o caso

- **GIVEN** um caso em `SCHEDULER_REQUESTED` sem reserva
- **WHEN** o agendador abre a página de confirmação
- **THEN** o caso fica reservado para ele (evento de claim na trilha) e o formulário carrega o token da reserva

#### Scenario: Outro agendador é barrado pela reserva ativa

- **GIVEN** um caso pronto para agendamento reservado pelo agendador da unidade 1
- **WHEN** o agendador da unidade 2 tenta abrir a confirmação
- **THEN** ele recebe mensagem de que o caso está reservado (com o portador) e é redirecionado à fila, sem alterar a reserva

#### Scenario: Sair sem decidir libera imediatamente

- **GIVEN** um caso pronto para agendamento reservado pelo agendador
- **WHEN** ele aciona «Sair sem decidir»
- **THEN** a reserva é liberada na hora e ele volta à fila com o caso disponível
