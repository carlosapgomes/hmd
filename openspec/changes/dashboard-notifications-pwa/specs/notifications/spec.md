# notifications Specification (delta)

## Purpose

Notificações in-app por marcos do ciclo do caso (resposta final, pronto para agendamento, reabertura por intercorrência), com badge de não lidas, lista com janela de visibilidade e redirecionamento contextual ao abrir — sem PHI no conteúdo.

## ADDED Requirements

### Requirement: Notificações por marcos do caso

O sistema SHALL criar notificação in-app quando eventos-marco da FSM ocorrerem: resposta final publicada e reabertura por intercorrência notificam o criador do caso; caso pronto para agendamento notifica todos os usuários com papel scheduler. As notificações SHALL ser idempotentes (evento reprocessado não duplica), SHALL conter apenas texto fixo e identificador do caso (sem dados de paciente) e uma falha na criação SHALL jamais impedir a transição do caso.

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

#### Scenario: Reprocessamento não duplica

- **GIVEN** uma notificação já criada para um evento
- **WHEN** o mesmo evento é processado novamente
- **THEN** nenhuma notificação duplicada existe para o mesmo destinatário

#### Scenario: Falha na notificação não bloqueia o caso

- **GIVEN** uma falha ao criar a notificação de um marco
- **WHEN** a transição do caso é concluída
- **THEN** o caso segue seu fluxo normalmente e a falha é registrada em log

### Requirement: Badge, lista e leitura de notificações

O sistema SHALL exibir em toda página autenticada um sino com a contagem de não lidas do usuário, e uma página de notificações listando não lidas e leituras recentes (janela de retenção configurável, nada apagado). Abrir uma notificação SHALL marcá-la como lida e redirecionar ao caso pela visão do papel ativo; marcar-todas SHALL ler todas as não lidas do usuário; acessos SHALL ficar restritos ao destinatário.

#### Scenario: Badge na navbar

- **GIVEN** um usuário com 3 notificações não lidas
- **WHEN** ele abre qualquer página autenticada
- **THEN** o sino exibe a contagem 3

#### Scenario: Lista aplica janela de visibilidade

- **GIVEN** uma notificação não lida antiga e outra lida há mais de 48 horas
- **WHEN** o usuário abre a lista
- **THEN** a não lida aparece e a leitura antiga não (permanece no banco)

#### Scenario: Abrir marca como lida e redireciona

- **GIVEN** uma notificação não lida do usuário
- **WHEN** ele a abre com papel ativo nir
- **THEN** ela fica lida e o redirecionamento leva ao detalhe do caso na visão do NIR

#### Scenario: Notificação de outro usuário é inacessível

- **GIVEN** uma notificação destinada a outro usuário
- **WHEN** este usuário tenta abri-la
- **THEN** recebe HTTP 404 e a notificação não é marcada como lida

#### Scenario: Marcar todas como lidas

- **GIVEN** 3 notificações não lidas do usuário
- **WHEN** ele aciona marcar-todas
- **THEN** todas ficam lidas e o badge passa a zero
