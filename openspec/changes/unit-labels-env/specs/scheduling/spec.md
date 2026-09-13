# scheduling Specification (delta)

## MODIFIED Requirements

### Requirement: Confirmação de agendamento com unidade e resposta final

O sistema SHALL confirmar o agendamento de um caso em `SCHEDULER_REQUESTED` ou `AWAITING_SCHEDULING` registrando unidade (1 ou 2), data/hora e local, encadeando as transições até `FINAL_REPLY_POSTED` no mesmo atomic (na origem `AWAITING_SCHEDULING` a transição de entrada de fila não é re-disparada) e postando na thread do caso a resposta final ao NIR: para unidade 1, o texto padrão com local e data/hora; para unidade 2, exatamente "Recusar o relatório — caso agendado na {label da unidade 2 configurada}, que comunicará a Secretaria" (com os defaults de ambiente: "Recusar o relatório — caso agendado na Unidade 2, que comunicará a Secretaria"). Data/hora no passado é rejeitada.

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

## ADDED Requirements

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
