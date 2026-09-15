# Delta: doctor-decision

## MODIFIED Requirements

### Requirement: Fila médica com filtro por estado e subtipo

O sistema SHALL exibir a fila de casos para o papel ativo `doctor`/`admin`,
segmentada por estado (`aguardando decisão` = `AWAITING_DOCTOR`; `decididos` =
estados pós-decisão), ordenada por **tempo de tela** — `days_on_screen`
(descendente, casos sem o campo ao fim), desempate FIFO por `created_at` —
com filtro por subtipo de
procedimento: `Todas` + os subtipos atribuídos ao usuário (generalista sem
subtipo e `admin` veem `Todas` e qualquer subtipo). Os cards SHALL exibir a
identificação do paciente (nome e idade quando presentes), nº de ocorrência,
tempo de espera (desde `created_at`) e o tempo de tela oficial do cabeçalho
(dias em tela) quando presente. Outros papéis ativos
recebem 403.

#### Scenario: Médico de subtipo filtra a fila

- **GIVEN** um médico com subtipo `angio` e casos em `AWAITING_DOCTOR` de subtipos `angio` e `cardio`
- **WHEN** acessa a fila com o filtro `angio`
- **THEN** apenas casos com ao menos um tipo declarado de subtipo `angio` são listados

#### Scenario: Generalista vê qualquer subtipo

- **GIVEN** um médico sem subtipos atribuídos e casos em `AWAITING_DOCTOR` de subtipos distintos
- **WHEN** acessa a fila com o filtro `Todas`
- **THEN** casos de todos os subtipos são listados

#### Scenario: Papel não médico recebe 403

- **GIVEN** usuário com papel ativo `nir`
- **WHEN** acessa a fila médica
- **THEN** recebe HTTP 403 sem listagem

#### Scenario: Fila ordenada pelo tempo de tela

- **GIVEN** casos em `AWAITING_DOCTOR` com `days_on_screen` 10, 3 e um sem cabeçalho (upload mais antigo)
- **WHEN** o médico acessa a fila
- **THEN** a ordem é 10, 3 e o caso sem cabeçalho ao fim (desempate FIFO), independentemente do momento do upload

#### Scenario: Card com identificação e tempo de espera

- **GIVEN** um caso em `AWAITING_DOCTOR` com nome, idade 84 e `days_on_screen` 6
- **WHEN** o médico acessa a fila (aba aguardando)
- **THEN** o card exibe nome e idade (`84 a`), o rótulo «Aguardando há …» com o tempo desde o recebimento e o badge `6 d em tela`

#### Scenario: Aba decidida usa rótulo de recebimento

- **GIVEN** um caso decidido com nome e idade
- **WHEN** o médico acessa a aba decididos
- **THEN** o card exibe o rótulo «Recebido há …» (sem «Aguardando», que não faz sentido em histórico) e a idade junto do nome
