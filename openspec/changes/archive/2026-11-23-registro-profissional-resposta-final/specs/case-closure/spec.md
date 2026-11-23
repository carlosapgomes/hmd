# Spec Delta

## MODIFIED Requirements

### Requirement: Resposta final de negativa médica

Quando o médico nega todos os procedimentos declarados, o sistema SHALL publicar automaticamente a resposta final ao NIR na thread do caso — com o motivo de cada procedimento negado — levando o caso a `FINAL_REPLY_POSTED` no mesmo atomic. A resposta SHALL encerrar com a linha do médico responsável pela decisão: `Médico responsável: {nome do médico decisor}` seguido do registro de conselho formatado quando existir (`Médico responsável: {nome} — {conselho} {número}`; ex.: `CRM 12345`); sem registro preenchido, apenas o nome; sem médico decisor registrado, a linha é omitida. Decisão parcial (ao menos um procedimento aprovado) SHALL seguir para o agendamento sem publicar resposta final.

#### Scenario: Negação total publica resposta com motivos

- **GIVEN** um caso em `AWAITING_DOCTOR` com dois procedimentos declarados
- **WHEN** o médico nega ambos informando os motivos
- **THEN** o caso chega a `FINAL_REPLY_POSTED` e a thread contém a resposta final citando cada procedimento negado com seu motivo, encerrada com a última linha `Médico responsável: {nome} — CRM {número}` quando o registro do médico decisor está preenchido

#### Scenario: Negação total com médico sem registro degrada para nome apenas

- **GIVEN** um caso em `AWAITING_DOCTOR` decidido por um médico sem conselho/número preenchidos
- **WHEN** o médico nega todos os procedimentos
- **THEN** a resposta final publicada encerra com `Médico responsável: {nome}` sem registro, e a postagem não falha

#### Scenario: Decisão parcial não publica resposta final

- **GIVEN** um caso em `AWAITING_DOCTOR` com dois procedimentos declarados
- **WHEN** o médico aprova um e nega outro
- **THEN** o caso segue para `SCHEDULER_REQUESTED` e nenhuma resposta final é publicada

#### Scenario: Estado fora da negativa é recusado

- **GIVEN** um caso que não está em `DOCTOR_DENIED`
- **WHEN** o serviço de resposta de negativa é invocado
- **THEN** a operação é recusada com erro nomeado e nada muda
