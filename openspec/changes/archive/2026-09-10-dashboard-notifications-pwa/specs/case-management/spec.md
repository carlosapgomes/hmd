# case-management Specification (delta)

## MODIFIED Requirements

### Requirement: Comunicações operacionais por caso

Cada caso SHALL ter uma thread de comunicações append-only com dois tipos de mensagem: `user` (manual, com autor e papel ativo no momento do post) e `system` (automática, sem autor, projetada a partir de eventos relevantes da FSM). Mensagens `system` não geram notificação nem estado de leitura; notificações in-app (capability `notifications`) derivam diretamente de eventos da FSM, não de mensagens da thread — a thread permanece sem estado de leitura.

#### Scenario: Mensagem de usuário registra autor e papel

- **GIVEN** um usuário autenticado com papel ativo `scheduler`
- **WHEN** posta uma mensagem no caso
- **THEN** a mensagem é gravada com autor, papel ativo `scheduler` e timestamp, e aparece na thread em ordem

#### Scenario: Evento FSM projeta mensagem sistêmica

- **GIVEN** um caso cuja transição é de um tipo que exige comunicação sistêmica (ex.: chegada à fila médica)
- **WHEN** a transição ocorre
- **THEN** uma mensagem `system` é criada vinculada ao evento, sem autor e sem notificação

#### Scenario: Notificação deriva do evento sem tocar a thread

- **GIVEN** um evento-marco com notificação configurada (ex.: resposta final publicada)
- **WHEN** o evento é gravado
- **THEN** a notificação in-app é criada a partir do evento e nenhuma mensagem da thread carrega estado de leitura ou notificação
