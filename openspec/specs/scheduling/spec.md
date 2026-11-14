# scheduling Specification

## Purpose

Confirmar, negar e reabrir o agendamento de casos aceitos pelo médico nas duas unidades do serviço, com resposta final diferenciada ao NIR e trilha auditável — o estágio do caso entre a decisão médica e a ciência do NIR.

## Requirements

### Requirement: Fila do agendador completa

O sistema SHALL exibir ao papel ativo `scheduler`/`admin` a fila de casos prontos para agendamento (estados `SCHEDULER_REQUESTED` e `AWAITING_SCHEDULING` — pedidos novos e casos reabertos por intercorrência), ordenada por **tempo de tela** — `days_on_screen` (descendente, casos sem o campo ao fim), desempate FIFO por `created_at` — paginada, **sem diferenciação por unidade**, com aba de casos processados do agendamento; os cards exibem nome e idade do paciente (quando presentes), nº de ocorrência, tempo de espera e o tempo de tela oficial quando presente. Antes de montar a aba aguardando, o sistema SHALL expirar reservas vencidas dos casos nesses estados; cada card dessa aba SHALL refletir o estado de reserva: sem reserva → ação livre que abre a página de confirmação; reservado pelo próprio agendador → ação «Continuar»; reservado por outro portador → botão desabilitado «Reservado» com o nome do portador. Outros papéis ativos recebem 403; anônimo é redirecionado ao login. Os cards NÃO exibem o identificador interno do caso (uid). A apresentação segue o padrão ats-web: abas `Pendentes/Processados`, visual `case-card` com alerta de espera, badges de contagem nas abas, busca client-side por nome/ocorrência e atualização automática via partial a cada 20 s; ordenação, guards e paginação server-side preservados.

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

### Requirement: Negação de agendamento com motivo obrigatório

O sistema SHALL permitir negar o agendamento de um caso pronto (`SCHEDULER_REQUESTED` ou `AWAITING_SCHEDULING`), exigindo motivo não vazio, encadeando as transições até `FINAL_REPLY_POSTED` no mesmo atomic e postando ao NIR a resposta final com o motivo informado.

#### Scenario: Negação publica motivo ao NIR

- **GIVEN** um caso em `SCHEDULER_REQUESTED`
- **WHEN** o agendador nega com motivo informado
- **THEN** o caso chega a `FINAL_REPLY_POSTED` com o motivo persistido e a resposta final ao NIR contém o motivo

#### Scenario: Negação sem motivo é rejeitada

- **GIVEN** um caso em `SCHEDULER_REQUESTED`
- **WHEN** o agendador nega com motivo em branco
- **THEN** o formulário rejeita com erro de validação e nada é persistido

### Requirement: Intercorrência pós-agendamento apenas na unidade 1

O sistema SHALL permitir desmarcar, por intercorrência, um caso confirmado na unidade 1 que ainda não teve ciência do NIR: motivo obrigatório, transição de reabertura para `AWAITING_SCHEDULING` no mesmo atomic, limpeza dos dados de agendamento, evento auditável e comunicação ao NIR informando o retorno à fila. Casos agendados na unidade 2 SHALL ter a intercorrência desabilitada; após re-confirmação o ciclo pode se repetir.

#### Scenario: Intercorrência na unidade 1 retorna o caso à fila

- **GIVEN** um caso em `FINAL_REPLY_POSTED` confirmado na unidade 1
- **WHEN** o agendador registra intercorrência com motivo
- **THEN** o caso volta a `AWAITING_SCHEDULING` com os dados de agendamento limpos, evento na trilha e comunicação ao NIR sobre o retorno à fila

#### Scenario: Intercorrência na unidade 2 é bloqueada

- **GIVEN** um caso em `FINAL_REPLY_POSTED` confirmado na unidade 2
- **WHEN** o agendador tenta registrar intercorrência
- **THEN** a operação é recusada com erro nomeado e o caso permanece inalterado

#### Scenario: Reconfirmação após intercorrência

- **GIVEN** um caso reaberto por intercorrência em `AWAITING_SCHEDULING`
- **WHEN** o agendador confirma novo agendamento na unidade 1
- **THEN** o caso chega a `FINAL_REPLY_POSTED` com os novos dados e nova resposta final ao NIR

### Requirement: Visão do agendador limitada ao necessário

O detalhe do caso para o agendador SHALL exibir apenas a identificação do paciente (nome, data de nascimento, número de ocorrência), o diagnóstico resumido (primeira linha do sumário, re-identificada apenas na renderização), os procedimentos com as decisões médicas (incluindo motivos das negativas), os dados de agendamento e a thread de comunicações — sem os demais artefatos do pipeline (linhas seguintes do sumário, estrutura extraída, alertas da policy, sugestão). O PDF do relatório original SHALL ficar disponível ao agendador somente após a decisão de agendamento, para casos processados por ele mesmo — por documento do relatório (o relatório pode chegar em múltiplos PDFs ordenados).

#### Scenario: Agendador vê o diagnóstico resumido re-identificado

- **GIVEN** um caso com `summary_text` de múltiplas linhas contendo pseudônimos e o mapa de pseudônimos do caso
- **WHEN** o agendador abre o detalhe do agendamento
- **THEN** apenas a primeira linha do sumário é exibida, já re-identificada (sem tokens), e nenhuma outra linha do sumário aparece na página

#### Scenario: Agendador não vê artefatos clínicos além do permitido

- **GIVEN** um caso com sumário completo, estrutura extraída e alertas da policy persistidos
- **WHEN** o agendador abre o detalhe do agendamento
- **THEN** identificação, diagnóstico resumido e decisões médicas são exibidas e nenhum conteúdo de estrutura extraída, alerta da policy ou sugestão aparece na página

#### Scenario: PDF disponível apenas de caso processado pelo próprio agendador

- **GIVEN** um caso confirmado pelo agendador autenticado, com dois documentos de relatório
- **WHEN** ele solicita o PDF de cada documento do caso
- **THEN** cada documento original é servido como PDF

#### Scenario: PDF negado a outro agendador ou a caso reaberto

- **GIVEN** um caso confirmado por outro agendador e um caso reaberto por intercorrência (sem agendador vinculado)
- **WHEN** o agendador autenticado solicita o PDF de qualquer um deles
- **THEN** recebe HTTP 404 e nenhum conteúdo do documento é exposto

### Requirement: Rótulos de unidade configuráveis por ambiente

O sistema SHALL exibir os nomes das unidades de agendamento a partir das variáveis de ambiente `HMD_UNIT_1_LABEL` e `HMD_UNIT_2_LABEL` (defaults "Unidade 1" e "Unidade 2"), com fonte única de resolução, em todos os pontos de exibição: choices do formulário de confirmação, resposta final ao NIR da unidade 2, painel, detail do caso e manual do usuário. O valor persistido de agendamento continua sendo o código da unidade (1 ou 2); labels não alteram validação, FSM ou dados históricos já gravados.

#### Scenario: Labels configurados refletem em toda a exibição

- **GIVEN** `HMD_UNIT_1_LABEL=Hemodinâmica HGRS` e `HMD_UNIT_2_LABEL=Unidade Satélite`
- **WHEN** um agendador abre o formulário de confirmação, confirma um caso na unidade 2, e o NIR/manager visualiza o painel e o manual
- **THEN** o formulário, a resposta final postada, o painel e o manual exibem os labels configurados (a resposta final da unidade 2 cita "Unidade Satélite")

#### Scenario: Defaults mantêm o comportamento atual

- **GIVEN** as variáveis de ambiente de labels não definidas (ou vazias)
- **WHEN** qualquer fluxo de agendamento é executado
- **THEN** todas as exibições usam "Unidade 1"/"Unidade 2" e os textos permanecem idênticos aos atuais

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
