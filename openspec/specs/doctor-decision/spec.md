# doctor-decision Specification

## Purpose

Apresentar ao médico, com dados reais do paciente e sob controle de acesso por
subtipo, os casos prontos para decisão — com os alertas e a recomendação
consultiva da policy — e registrar a decisão por procedimento com trilha
auditável, fechando o estágio entre a sumarização e o agendamento.

## Requirements

### Requirement: Fila médica com filtro por estado e subtipo

O sistema SHALL exibir a fila de casos para o papel ativo `doctor`/`admin`,
segmentada por estado (`aguardando decisão` = `AWAITING_DOCTOR`; `decididos` =
estados pós-decisão), ordenada por chegada (FIFO), com filtro por subtipo de
procedimento: `Todas` + os subtipos atribuídos ao usuário (generalista sem
subtipo e `admin` veem `Todas` e qualquer subtipo). Outros papéis ativos
recebem 403.

#### Scenario: Médico de subtipo filtra a fila

- **GIVEN** um médico com subtipo `angio` e casos em `AWAITING_DOCTOR` de subtipos `angio` e `cardio`
- **WHEN** acessa a fila com o filtro `angio`
- **THEN** apenas casos com ao menos um tipo declarado de subtipo `angio` são listados, ordenados por `created_at`

#### Scenario: Generalista vê qualquer subtipo

- **GIVEN** um médico sem subtipos atribuídos e casos em `AWAITING_DOCTOR` de subtipos distintos
- **WHEN** acessa a fila com o filtro `Todas`
- **THEN** casos de todos os subtipos são listados

#### Scenario: Papel não médico recebe 403

- **GIVEN** usuário com papel ativo `nir`
- **WHEN** acessa a fila médica
- **THEN** recebe HTTP 403 sem listagem

### Requirement: Access control por subtipo no conjunto declarado

O sistema SHALL restringir médico com subtipos atribuídos a decidir apenas
casos cujo conjunto de procedimentos declarados inclua ao menos um tipo do seu
subtipo; generalista (sem subtipo) e `admin` acessam qualquer caso. A checagem
SHALL ser aplicada na abertura do detalhe e reforcada no envio da decisão —
nunca apenas no template.

#### Scenario: Médico de subtipo abre caso fora do seu subtipo

- **GIVEN** médico com subtipo `cardio` e um caso declarado apenas com tipos de subtipo `angio`
- **WHEN** tenta abrir o detalhe de decisão
- **THEN** recebe HTTP 403 sem exposição de dados do paciente

#### Scenario: Tentativa de decisão fora do subtipo é rejeitada

- **GIVEN** o mesmo médico e caso
- **WHEN** envia diretamente o POST de decisão do caso
- **THEN** a submissão é rejeitada com HTTP 403 e nenhuma decisão é persistida

### Requirement: Presenter re-identificado sob papel autorizado

O detalhe do caso SHALL apresentar, apenas para `doctor`/`admin`, os dados
reais do paciente (identificação, número de ocorrência) e os artefatos do
pipeline re-identificados: histórico e sumário, estrutura extraída, alertas da
policy com a recomendação por procedimento e o resultado agregado, requisitos
gerais acionáveis e o PDF original. A re-identificação SHALL ocorrer apenas na
renderização para papel autorizado; nenhum artefato re-identificado é
persistido nem enviado a qualquer LLM.

#### Scenario: Médico vê o sumário re-identificado

- **GIVEN** um caso em `AWAITING_DOCTOR` cujo `summary_text` contém o token `<PESSOA_1>`
- **WHEN** o médico autorizado abre o detalhe
- **THEN** o sumário é renderizado com o nome real do paciente no lugar do token

#### Scenario: Requisitos gerais acionáveis exibidos conforme alertas

- **GIVEN** um caso cuja policy gerou alerta de anticoagulante com protocolo de suspensão por fármaco
- **WHEN** o médico abre o detalhe
- **THEN** o card do procedimento exibe o requisito geral com o protocolo acionável correspondente ao alerta

#### Scenario: Recomendação consultiva visível por procedimento

- **GIVEN** um caso com `policy_result` recomendando recusar um procedimento com motivos e a sugestão agregada de recusa
- **WHEN** o médico abre o detalhe
- **THEN** os motivos da recusa por procedimento e o agregado são exibidos como alerta consultivo, sem bloquear a decisão

### Requirement: Card de prior-case com motivo real

O detalhe do caso SHALL exibir, por procedimento declarado com caso prévio
encontrado (número de ocorrência ou fallback nome+nascimento), um card com
data, desfecho e motivo real da decisão prévia. O lookup do presenter é
somente-leitura (eventos de lookup permanecem os do pipeline) e respeita as
mesmas janelas configuráveis do pipeline.

#### Scenario: Card de negativa prévia por número de ocorrência

- **GIVEN** um caso cujo procedimento tem caso prévio negado há 3 dias com o mesmo número de ocorrência
- **WHEN** o médico abre o detalhe
- **THEN** o card exibe data, desfecho negado e o motivo real registrado pelo médico prévio

#### Scenario: PDF original restrito a papel autorizado

- **GIVEN** um caso com documento PDF
- **WHEN** um usuário com papel ativo `nir` tenta acessar a URL do PDF médico diretamente
- **THEN** recebe HTTP 403 sem conteúdo do arquivo

#### Scenario: Card de prior-case por fallback nome+nascimento

- **GIVEN** um caso cujo procedimento tem caso prévio negado há 10 dias, número de ocorrência diferente, mesmo nome normalizado e nascimento
- **WHEN** o médico abre o detalhe
- **THEN** o card exibe o prévio com desfecho e motivo real, marcando a origem do match

### Requirement: Decisão por procedimento com motivo obrigatório em negativas

O sistema SHALL registrar a decisão médica por procedimento declarado
(`approved`/`denied`) com motivo obrigatório quando negado, validando no
formulário e persistindo rows, evento auditável e transição FSM no mesmo
atomic (todos negados → `DOCTOR_DENIED`; ao menos um aprovado →
`DOCTOR_ACCEPTED` com encadeamento para a fila de agendamento). Submissão
concorrente ou fora de `AWAITING_DOCTOR` SHALL falhar sem efeito parcial,
com mensagem clara ao usuário.

#### Scenario: Negação exige motivo

- **GIVEN** o formulário com um procedimento marcado `denied` e motivo em branco
- **WHEN** o médico envia
- **THEN** o formulário rejeita com erro de validação e nada é persistido

#### Scenario: Decisão mista leva o caso ao agendamento

- **GIVEN** um caso com dois procedimentos declarados em `AWAITING_DOCTOR`
- **WHEN** o médico aprova um e nega o outro com motivo
- **THEN** as rows registram as disposições, o evento resume as decisões e o caso transita para a fila de agendamento

#### Scenario: Dupla submissão falha sem efeito parcial

- **GIVEN** um caso que acabou de ser decidido por outro médico
- **WHEN** uma segunda submissão chega para o mesmo caso
- **THEN** ela falha com mensagem de estado e nenhuma decisão adicional é gravada

### Requirement: Caso decidido consultável

O sistema SHALL exibir, para `doctor`/`admin` com acesso ao caso (regra de
subtipo), o detalhe read-only de casos decididos com as decisões por
procedimento, motivos, ator e data, além da trilha de eventos — sem permitir
nova decisão fora de `AWAITING_DOCTOR`.

#### Scenario: Detalhe decidido read-only

- **GIVEN** um caso em `DOCTOR_DENIED`
- **WHEN** o médico autorizado abre o detalhe
- **THEN** as decisões, motivos e a trilha são exibidos e nenhum formulário de decisão é renderizado
