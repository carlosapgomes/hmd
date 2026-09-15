# Delta: dashboard

## MODIFIED Requirements

### Requirement: Métricas por período sem dados de paciente

O sistema SHALL exibir um painel com métricas do período selecionado (hoje,
7 dias, 30 dias, tudo) computadas a partir de fontes imutáveis (evento de
resposta final com sua origem, decisões por procedimento, unidade
agendada), de modo que casos encerrados e limpos continuem contados —
INCLUINDO a contagem de casos encerrados administrativamente no período.
A **seção de métricas** SHALL conter apenas contagens, tempos médios e
labels de tipo/unidade — nenhum dado de paciente (a listagem de casos da
mesma página exibe a identificação completa do paciente — invariante
próprio no requisito "Lista de casos no painel"; o invariante de PHI aplica-se
apenas à seção de métricas). Casos encerrados administrativamente SHALL sair
da contagem de "em andamento".

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
- **THEN** na seção de métricas, nenhum nome ou número de registro de
  paciente aparece (a lista de casos exibe a identificação do paciente —
  o invariante de PHI é da seção de métricas)

#### Scenario: Período selecionável

- **GIVEN** casos criados em dias distintos
- **WHEN** o período 7 dias é selecionado
- **THEN** apenas os casos do período entram nas métricas

#### Scenario: Encerramentos administrativos contados no período

- **GIVEN** casos encerrados administrativamente dentro do período
- **WHEN** o painel é aberto
- **THEN** a métrica de encerramentos administrativos reflete a contagem do
  período, esses casos não aparecem como "em andamento" e seguem contados
  como encerrados

### Requirement: Lista de casos no painel

O painel SHALL exibir, abaixo das métricas, a lista dos casos com
identificação completa do paciente e filtros que compõem por AND: `scope`
(`ativos|todos`, default `todos`), `status` (por status válido), `q`
(busca server-side por nome do paciente, número de ocorrência ou prefixo
do identificador, mínimo 3 caracteres — termos mais curtos ignorados),
`procedure_type` (dropdown «Tipo de exame» com os tipos do catálogo;
default todos) e `date_from`/`date_to` (busca por data de inserção). O
default SEM nenhum filtro explícito SHALL ser **hoje, todos os estados**
(incluindo `CLEANED`); qualquer filtro explícito é preservado. O `period`
das métricas permanece independente (hoje/7d/30d/tudo).

Cada card exibe: **nome do paciente** (`—` quando ausente) e **idade**
(`0 a` válido, omitida quando ausente), **unidade de origem** (quando
presente), nº de ocorrência, tipos de procedimento declarados, fase do
fluxo (status e próximo passo), **data/hora de inserção** e o botão
**[Detalhes]** (rota `dashboard:case_detail`). Casos com status diferente
de `CLEANED` oferecem o encerramento administrativo a partir do detalhe.
A listagem é server-side (re-renderização com filtros na query string),
ordenada por data de criação decrescente e paginada.

#### Scenario: Lista padrão mostra casos ativos do período

- **GIVEN** casos em diversos estados recebidos hoje, incluindo `CLEANED`
- **WHEN** o painel é acessado sem filtros
- **THEN** os casos recebidos hoje aparecem em todos os estados (incluindo
  `CLEANED`), em ordem de criação decrescente, com nome e idade nos cards

#### Scenario: Escopo todos inclui encerrados

- **GIVEN** casos `CLEANED` no período
- **WHEN** o painel é acessado com `?scope=todos`
- **THEN** os casos encerrados aparecem na lista

#### Scenario: Filtro por status

- **GIVEN** casos em diversos estados no período
- **WHEN** o painel é acessado com `?status=<um status válido>`
- **THEN** apenas casos nesse status aparecem

#### Scenario: Busca server-side por ocorrência ou prefixo

- **GIVEN** casos com números de ocorrência distintos
- **WHEN** o painel é acessado com `?q=` de ao menos 3 caracteres
- **THEN** a lista retorna apenas os casos cujo número de ocorrência casa ou
  cujo uid tem o termo como prefixo

#### Scenario: Busca curta é ignorada

- **GIVEN** casos no período
- **WHEN** o painel é acessado com `?q=` de menos de 3 caracteres
- **THEN** a lista ignora o termo e mostra o resultado sem o filtro de busca

#### Scenario: Lista paginada

- **GIVEN** mais casos no período do que o tamanho de página
- **WHEN** a lista é renderizada
- **THEN** apenas a primeira página aparece com navegação de paginação
  preservando os filtros

#### Scenario: Ação de encerramento só em casos não concluídos

- **GIVEN** a lista renderizada
- **WHEN** um caso `CLEANED` e um caso ativo são inspecionados
- **THEN** apenas o caso ativo oferece o encerramento administrativo (a partir do detalhe)

#### Scenario: Papel fora de manager/admin não acessa a lista

- **GIVEN** um usuário com papel ativo `nir`, `doctor` ou `scheduler`
- **WHEN** tenta acessar o painel
- **THEN** recebe 403

#### Scenario: Busca por nome do paciente

- **GIVEN** casos com pacientes identificados pelo cabeçalho SESAB
- **WHEN** o painel é acessado com `?q=` contendo parte do nome de um paciente
- **THEN** a lista retorna apenas os casos daquele paciente

#### Scenario: Busca por data de inserção

- **GIVEN** casos recebidos em dias distintos
- **WHEN** o painel é acessado com `?date_from=<ISO>` e/ou `?date_to=<ISO>`
- **THEN** a lista retorna apenas os casos inseridos no intervalo (compondo por AND com os demais filtros)

#### Scenario: Filtro por tipo de exame

- **GIVEN** casos com tipos de procedimento declarados distintos
- **WHEN** o painel é acessado com `?procedure_type=<um tipo do catálogo>`
- **THEN** apenas casos com aquele tipo declarado pelo NIR aparecem

#### Scenario: Card com identificação completa e fase

- **GIVEN** um caso com nome, idade 84, unidade de origem e tipos declarados
- **WHEN** a lista é renderizada
- **THEN** o card exibe nome, `84 a`, unidade de origem, exames declarados, fase (status + próximo passo), data/hora de inserção e o botão [Detalhes]

## ADDED Requirements

### Requirement: Detalhe do caso no painel com trilha legível

O painel SHALL exibir o detalhe de um caso (manager/admin) com a
identificação completa do paciente (nome, idade, sexo, raça/cor, unidade
de origem, nº de ocorrência, data/hora de inserção e fase/status) e os
procedimentos declarados, hospedando a ação de encerramento administrativo
para casos não concluídos. O detalhe SHALL incluir a **trilha de eventos
com rótulos legíveis** — mapa dos tipos de evento do sistema para
português claro (molde ats-web `EVENT_LABELS`; tipos fora do mapa exibem o
valor bruto), com data/hora e ator — em card **collapsible fechado por
padrão** (Bootstrap). Caso inexistente → 404 sem vazar informação.

#### Scenario: Detalhe com identificação completa

- **GIVEN** um caso identificado pelo cabeçalho SESAB
- **WHEN** o manager abre o detalhe pelo botão [Detalhes]
- **THEN** nome, idade, sexo, raça/cor, unidade de origem, nº de ocorrência, data/hora de inserção e fase aparecem, com os procedimentos declarados

#### Scenario: Trilha com rótulos legíveis collapsible

- **GIVEN** um caso com trilha de eventos em diversos tipos
- **WHEN** o detalhe é renderizado
- **THEN** a trilha aparece em card fechado por padrão; ao abrir, cada evento tem rótulo legível do mapa (fallback = valor bruto), data/hora e ator

#### Scenario: Encerramento administrativo a partir do detalhe

- **GIVEN** um caso ativo detalhado por manager/admin
- **WHEN** o detalhe é inspecionado
- **THEN** a ação de encerramento administrativo está disponível (fluxo existente preservado); caso `CLEANED` não oferece

#### Scenario: Caso inexistente não vaza

- **GIVEN** um identificador inexistente
- **WHEN** o detalhe é acessado
- **THEN** retorna 404 sem informação de outros casos
