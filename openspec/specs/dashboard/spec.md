# dashboard Specification

## Purpose

Painel gerencial com métricas agregadas do serviço de hemodinâmica por período, tipo de procedimento e unidade de agendamento — a seção de métricas não exibe dados de paciente; a listagem de casos identifica por número de ocorrência do caso (nome e nascimento do paciente não aparecem).

## Requirements

### Requirement: Métricas por período sem dados de paciente

O sistema SHALL exibir um painel com métricas do período selecionado (hoje,
7 dias, 30 dias, tudo) computadas a partir de fontes imutáveis (evento de
resposta final com sua origem, decisões por procedimento, unidade
agendada), de modo que casos encerrados e limpos continuem contados —
INCLUINDO a contagem de casos encerrados administrativamente no período.
A **seção de métricas** SHALL conter apenas contagens, tempos médios e
labels de tipo/unidade — nenhum dado de paciente (a listagem de casos da
mesma página tem invariante próprio no requisito "Lista de casos no
painel"). Casos encerrados administrativamente SHALL sair da contagem de
"em andamento".

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
  paciente aparece (a lista de casos identifica por nº de ocorrência do
  caso e não exibe nome nem data de nascimento do paciente)

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

O painel SHALL ser acessível apenas a usuários com papel ativo `manager` ou `admin`, por link na navbar visível somente para esses papéis, com o período preservado entre seleções. Outros papéis ativos autenticados recebem HTTP 403 na rota do painel; anônimo é redirecionado ao login.

#### Scenario: Usuário autenticado acessa o painel

- **GIVEN** um usuário autenticado com papel ativo `manager` ou `admin`
- **WHEN** abre o painel pelo link da navbar (visível apenas para esses papéis)
- **THEN** vê as métricas do período padrão

#### Scenario: Anônimo é redirecionado ao login

- **GIVEN** um usuário não autenticado
- **WHEN** abre a rota do painel
- **THEN** é redirecionado à tela de login

#### Scenario: Papel fora de manager/admin recebe 403

- **GIVEN** um usuário autenticado com papel ativo `nir` (ou `doctor`, ou `scheduler`)
- **WHEN** abre a rota do painel diretamente pela URL
- **THEN** recebe HTTP 403 e o link do painel não aparece na sua navbar

### Requirement: Lista de casos no painel

O painel SHALL exibir, abaixo das métricas, a lista dos casos do período com
escopo e filtros (`scope=ativos|todos`, default `ativos` = casos com status
diferente de `CLEANED`; `status` opcional por status válido; busca
server-side por número de ocorrência ou prefixo do identificador com no
mínimo 3 caracteres — termos mais curtos são ignorados), identificação por
número de ocorrência do caso (dado do caso, não do paciente) ou prefixo do
uid — **nome e data de nascimento do paciente não aparecem na lista**.
Cada caso exibe status, tipos de procedimento declarados, data de criação,
decisão/resultado quando existente e próximo passo. Casos com status
diferente de `CLEANED` SHALL oferecer a ação de encerramento
administrativo. A listagem SHALL ser server-side (re-renderização com
filtros na query string), ordenada por data de criação decrescente e
paginada.

#### Scenario: Lista padrão mostra casos ativos do período

- **GIVEN** casos em diversos estados no período, incluindo `CLEANED`
- **WHEN** o painel é acessado sem filtros
- **THEN** apenas casos com status diferente de `CLEANED` aparecem, em ordem
  de criação decrescente, sem nome de paciente

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
- **THEN** apenas o caso ativo exibe a ação de encerramento administrativo

#### Scenario: Papel fora de manager/admin não acessa a lista

- **GIVEN** um usuário com papel ativo `nir`, `doctor` ou `scheduler`
- **WHEN** tenta acessar o painel
- **THEN** recebe 403
