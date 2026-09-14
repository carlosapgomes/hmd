# dashboard Specification (delta)

## MODIFIED Requirements

### Requirement: Métricas por período sem dados de paciente

O sistema SHALL exibir um painel com métricas do período selecionado (hoje,
7 dias, 30 dias, tudo) computadas a partir de fontes imutáveis (evento de
resposta final com sua origem, decisões por procedimento, unidade
agendada), de modo que casos encerrados e limpos continuem contados —
INCLUINDO a contagem de casos encerrados administrativamente no período.
A página SHALL conter apenas contagens, tempos médios e labels de
tipo/unidade — nenhum dado de paciente.

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

#### Scenario: Encerramentos administrativos contados no período

- **GIVEN** casos encerrados administrativamente dentro do período
- **WHEN** o painel é aberto
- **THEN** a métrica de encerramentos administrativos reflete a contagem do
  período

## ADDED Requirements

### Requirement: Lista de casos no painel sem dados de paciente

O painel SHALL exibir, abaixo das métricas, a lista dos casos do período com
escopo e filtros (`scope=ativos|todos`, default `ativos` = casos com status
diferente de `CLEANED`; `status` opcional; busca server-side por número de
registro ou prefixo do identificador com no mínimo 3 caracteres), SEM dados
de paciente: cada caso exibe número de registro (ou prefixo do uid), status,
tipos de procedimento declarados, data de criação, decisão/resultado quando
existente e próximo passo. Casos com status diferente de `CLEANED` SHALL
oferecer a ação de encerramento administrativo. A listagem SHALL ser
server-side (re-renderização com filtros na query string), ordenada por data
de criação decrescente e paginada.

#### Scenario: Lista padrão mostra casos ativos do período

- **GIVEN** casos em diversos estados no período, incluindo `CLEANED`
- **WHEN** o painel é acessado sem filtros
- **THEN** apenas casos com status diferente de `CLEANED` aparecem, em ordem
  de criação decrescente, sem dados de paciente

#### Scenario: Escopo todos inclui encerrados

- **GIVEN** casos `CLEANED` no período
- **WHEN** o painel é acessado com `?scope=todos`
- **THEN** os casos encerrados aparecem na lista

#### Scenario: Busca server-side por registro ou prefixo

- **GIVEN** casos com números de registro distintos
- **WHEN** o painel é acessado com `?q=` de ao menos 3 caracteres
- **THEN** a lista retorna apenas os casos cujo número de registro casa ou
  cujo uid tem o termo como prefixo

#### Scenario: Ação de encerramento só em casos não concluídos

- **GIVEN** a lista renderizada
- **WHEN** um caso `CLEANED` e um caso ativo são inspecionados
- **THEN** apenas o caso ativo exibe a ação de encerramento administrativo

#### Scenario: Papel fora de manager/admin não acessa a lista

- **GIVEN** um usuário com papel ativo `nir`, `doctor` ou `scheduler`
- **WHEN** tenta acessar o painel
- **THEN** recebe 403
