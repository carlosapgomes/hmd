# notifications Specification (delta)

## MODIFIED Requirements

### Requirement: Notificações por marcos do caso

O sistema SHALL criar notificação in-app quando eventos-marco da FSM ocorrem:
resposta final publicada e reabertura por intercorrência notificam o criador
do caso; caso pronto para agendamento notifica todos os usuários com papel
scheduler; encerramento administrativo notifica o criador do caso. As
notificações SHALL ser idempotentes (evento reprocessado não duplica), SHALL
conter apenas texto fixo e identificador do caso (sem dados de paciente) e
uma falha na criação SHALL jamais impedir a transição do caso. Textos
canônicos (implementação): preview de agendamento = "Caso aguardando
confirmação de agendamento"; preview de reabertura = "Reconfirme os dados
do caso"; fallback de resposta final = "Resposta final disponível para o
caso"; título e preview de encerramento administrativo = "Caso encerrado
administrativamente" (o motivo NÃO entra na notificação — texto fixo).

#### Scenario: Resposta final notifica o criador

- **GIVEN** um caso cuja resposta final é publicada (negativa ou agendamento)
- **WHEN** o evento de resposta final é gravado
- **THEN** o criador do caso recebe uma notificação com título fixo e preview do tipo de resposta, sem dados de paciente

#### Scenario: Caso pronto notifica agendadores

- **GIVEN** um caso que chega ao estado de agendamento solicitado
- **WHEN** o evento é gravado
- **THEN** todos os usuários com papel scheduler recebem a notificação

#### Scenario: Reabertura por intercorrência notifica o criador

- **GIVEN** um caso respondido reaberto por intercorrência (evento de retorno à fila de agendamento com motivo)
- **WHEN** o evento é gravado
- **THEN** o criador recebe notificação de reabertura

#### Scenario: Encerramento administrativo notifica o criador

- **GIVEN** um caso encerrado administrativamente pelo supervisor
- **WHEN** o evento de encerramento administrativo é gravado
- **THEN** o criador do caso recebe notificação com título e preview fixos
  "Caso encerrado administrativamente", sem o motivo no texto

#### Scenario: Reprocessamento não duplica

- **GIVEN** uma notificação já criada para um evento
- **WHEN** o mesmo evento é processado novamente
- **THEN** nenhuma notificação duplicada existe para o mesmo destinatário

#### Scenario: Falha na notificação não bloqueia o caso

- **GIVEN** uma falha ao criar a notificação de um marco
- **WHEN** a transição do caso é concluída
- **THEN** o caso segue seu fluxo normalmente e a falha é registrada em log
