# Spec Delta

## MODIFIED Requirements

### Requirement: Confirmação de agendamento com unidade e resposta final

O sistema SHALL confirmar o agendamento de um caso em `SCHEDULER_REQUESTED` ou `AWAITING_SCHEDULING` registrando unidade (1 ou 2), data/hora e local, encadeando as transições até `FINAL_REPLY_POSTED` no mesmo atomic (na origem `AWAITING_SCHEDULING` a transição de entrada de fila não é re-disparada) e postando na thread do caso a resposta final ao NIR: para unidade 1, o texto padrão com local e data/hora; para unidade 2, o texto de recusa com encaminhamento à unidade 2 configurada/Secretaria ("Recusar o relatório — caso agendado na {label da unidade 2 configurada}, que comunicará a Secretaria"; com os defaults de ambiente: "Recusar o relatório — caso agendado na Unidade 2, que comunicará a Secretaria"). Em ambas as unidades, a resposta final SHALL encerrar com a linha do médico responsável pela decisão do caso: `Médico responsável: {nome do médico decisor}` seguido do registro de conselho formatado quando existir (`Médico responsável: {nome} — {conselho} {número}`; ex.: `CRM 12345`); sem registro preenchido, apenas o nome; sem médico decisor registrado, a linha é omitida. Data/hora no passado é rejeitada. A abertura da página de confirmação reserva o caso (contexto próprio do agendador) e a submissão exige reserva vigente (token de lock), liberando-a ao concluir com sucesso.

#### Scenario: Confirmação na unidade 1 publica resposta com data

- **GIVEN** um caso em `SCHEDULER_REQUESTED` decidido por um médico com conselho `CRM` e número preenchidos
- **WHEN** o agendador confirma com unidade 1, data/hora futura e local
- **THEN** o caso chega a `FINAL_REPLY_POSTED` com os campos de agendamento persistidos e a thread contém a resposta final com local, data/hora e a última linha `Médico responsável: {nome} — CRM {número}`

#### Scenario: Confirmação na unidade 2 publica recusa ao relatório

- **GIVEN** um caso em `SCHEDULER_REQUESTED` decidido por um médico com registro preenchido
- **WHEN** o agendador confirma com unidade 2
- **THEN** a resposta final postada é o texto de recusa com encaminhamento à unidade 2 configurada/Secretaria seguido da última linha do médico responsável com o registro

#### Scenario: Médico sem registro preenchido degrada para nome apenas

- **GIVEN** um caso em `SCHEDULER_REQUESTED` decidido por um médico sem conselho/número preenchidos
- **WHEN** o agendador confirma o agendamento em qualquer unidade
- **THEN** a resposta final termina com `Médico responsável: {nome}` sem registro, e a postagem não falha

#### Scenario: Data no passado é rejeitada

- **GIVEN** um caso em `SCHEDULER_REQUESTED`
- **WHEN** a confirmação usa data/hora no passado
- **THEN** a submissão é rejeitada com erro de validação e nada é persistido

#### Scenario: Confirmação sem reserva vigente falha

- **GIVEN** um caso em `SCHEDULER_REQUESTED` cujo token de reserva está ausente, inválido ou expirado
- **WHEN** o agendador envia a confirmação
- **THEN** o formulário é reexibido com mensagem de reserva expirada e nada é persistido
