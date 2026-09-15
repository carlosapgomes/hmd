# Delta: scheduling

## MODIFIED Requirements

### Requirement: Fila do agendador completa

O sistema SHALL exibir ao papel ativo `scheduler`/`admin` a fila de casos prontos para agendamento (estados `SCHEDULER_REQUESTED` e `AWAITING_SCHEDULING` — pedidos novos e casos reabertos por intercorrência), ordenada por **tempo de tela** — `days_on_screen` (descendente, casos sem o campo ao fim), desempate FIFO por `created_at` — paginada, **sem diferenciação por unidade**, com aba de casos processados do agendamento; os cards exibem nome e idade do paciente (quando presentes), nº de ocorrência, tempo de espera e o tempo de tela oficial quando presente. Outros papéis ativos recebem 403; anônimo é redirecionado ao login.

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
- **WHEN** o agendador acessa a fila
- **THEN** o card exibe nome e idade (`84 a`), o tempo de espera desde o recebimento e o badge `6 d em tela`
