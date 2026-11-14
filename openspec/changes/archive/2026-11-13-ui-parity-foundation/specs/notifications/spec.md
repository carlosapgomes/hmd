# Delta: notifications

## MODIFIED Requirements

### Requirement: Badge, lista e leitura de notificações

O sistema SHALL exibir em toda página autenticada um sino com a
contagem de não lidas do usuário, atualizada via polling sem
recarregar a página, e uma página de notificações listando não lidas
e leituras recentes (janela de retenção configurável, nada apagado).
Abrir uma notificação SHALL marcá-la como lida e redirecionar ao caso
pela visão do papel ativo; cada item SHALL oferecer leitura
individual; marcar-todas SHALL ler todas as não lidas do usuário;
acessos SHALL ficar restritos ao destinatário.

#### Scenario: Badge na navbar

- **GIVEN** um usuário com 3 notificações não lidas
- **WHEN** ele abre qualquer página autenticada
- **THEN** o sino exibe a contagem 3

#### Scenario: Polling atualiza o sino

- **GIVEN** página aberta com sino em 3 e uma 4ª notificação criada
- **WHEN** decorrem até 60 segundos
- **THEN** o sino exibe 4 sem recarregar a página

#### Scenario: Lista aplica janela de visibilidade

- **GIVEN** uma notificação não lida antiga e outra lida há mais de
  48 horas
- **WHEN** o usuário abre a lista
- **THEN** a não lida aparece e a leitura antiga não (permanece no
  banco)

#### Scenario: Abrir marca como lida e redireciona

- **GIVEN** uma notificação não lida do usuário
- **WHEN** ele a abre com papel ativo nir
- **THEN** ela fica lida e o redirecionamento leva ao detalhe do caso
  na visão do NIR

#### Scenario: Leitura individual

- **GIVEN** 3 notificações não lidas do usuário
- **WHEN** ele marca uma como lida sem abri-la
- **THEN** só ela fica lida e o badge passa a 2

#### Scenario: Notificação de outro usuário é inacessível

- **GIVEN** uma notificação destinada a outro usuário
- **WHEN** este usuário tenta abri-la
- **THEN** recebe HTTP 404 e a notificação não é marcada como lida

#### Scenario: Marcar todas como lidas

- **GIVEN** 3 notificações não lidas do usuário
- **WHEN** ele aciona marcar-todas
- **THEN** todas ficam lidas e o badge passa a zero

