# dashboard Specification (delta)

## Purpose

Painel gerencial com métricas agregadas do serviço de hemodinâmica por período, tipo de procedimento e unidade de agendamento — sem qualquer dado de paciente.

## ADDED Requirements

### Requirement: Métricas por período sem dados de paciente

O sistema SHALL exibir um painel com métricas do período selecionado (hoje, 7 dias, 30 dias, tudo) computadas a partir de fontes imutáveis (evento de resposta final com sua origem, decisões por procedimento, unidade agendada), de modo que casos encerrados e limpos continuem contados. A página SHALL conter apenas contagens, tempos médios e labels de tipo/unidade — nenhum dado de paciente.

#### Scenario: Resumo do período

- **GIVEN** casos no período (agendados, negados e em andamento)
- **WHEN** o painel é aberto no período
- **THEN** exibe total, agendados, negados e em andamento coerentes com os casos

#### Scenario: Casos limpos continuam contados

- **GIVEN** um caso encerrado e limpo pela ciência do NIR dentro do período
- **WHEN** o painel é aberto
- **THEN** o caso segue contado pelo seu resultado final (agendado ou negado)

#### Scenario: Página sem dados de paciente

- **GIVEN** casos com nomes e números de registro
- **WHEN** o painel é renderizado
- **THEN** nenhum nome ou número de registro de paciente aparece na página

#### Scenario: Período selecionável

- **GIVEN** casos criados em dias distintos
- **WHEN** o período 7 dias é selecionado
- **THEN** apenas os casos do período entram nas métricas

### Requirement: Quebra por tipo de procedimento e unidade

O sistema SHALL quebrar as métricas por tipo de procedimento (total, aprovados, negados, sem decisão, na ordem canônica do catálogo) e por unidade de agendamento (unidade 1 e unidade 2), além do tempo médio do caso até a decisão médica no período.

#### Scenario: Tabela por tipo com decisões

- **GIVEN** casos com procedimentos declarados e decididos
- **WHEN** o painel é aberto
- **THEN** a tabela por tipo exibe total, aprovados, negados e sem decisão por tipo de procedimento

#### Scenario: Agendados por unidade

- **GIVEN** casos agendados nas unidades 1 e 2
- **WHEN** o painel é aberto
- **THEN** a quebra por unidade exibe a contagem de cada unidade

#### Scenario: Tempo médio até a decisão

- **GIVEN** casos com decisão médica registrada no período
- **WHEN** o painel é aberto
- **THEN** exibe o tempo médio do início do caso até a decisão médica de forma legível

### Requirement: Acesso ao painel

O painel SHALL ser acessível a qualquer usuário autenticado por link na navbar, com o período preservado entre seleções e sem depender de papel ativo específico.

#### Scenario: Usuário autenticado acessa o painel

- **GIVEN** qualquer usuário autenticado em qualquer papel ativo
- **WHEN** abre o painel pelo link da navbar
- **THEN** vê as métricas do período padrão

#### Scenario: Anônimo é redirecionado ao login

- **GIVEN** um visitante sem sessão
- **WHEN** acessa a URL do painel
- **THEN** é redirecionado ao login
