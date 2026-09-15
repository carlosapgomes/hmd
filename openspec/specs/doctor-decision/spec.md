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
estados pós-decisão), ordenada por **tempo de tela** — `days_on_screen`
(descendente, casos sem o campo ao fim), desempate FIFO por `created_at` —
com filtro por subtipo de
procedimento: `Todas` + os subtipos atribuídos ao usuário (generalista sem
subtipo e `admin` veem `Todas` e qualquer subtipo). Os cards SHALL exibir a
identificação do paciente (nome e idade quando presentes), nº de ocorrência,
tempo de espera (desde `created_at`) e o tempo de tela oficial do cabeçalho
(dias em tela) quando presente. Outros papéis ativos
recebem 403. Os cards NÃO exibem o identificador interno do caso (uid).

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

#### Scenario: Cards sem o identificador interno do caso

- **GIVEN** casos com uid interno e nº de ocorrência
- **WHEN** a fila é renderizada
- **THEN** nenhum card exibe o uid do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores do card

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
O detalhe do caso SHALL apresentar, apenas para `doctor`/`admin`, os dados reais do paciente (identificação, número de ocorrência) e a demografia do caso — idade, sexo e raça/cor extraídas do cabeçalho padrão SESAB — e os artefatos do pipeline re-identificados: histórico e sumário, estrutura extraída, alertas da policy com a recomendação por procedimento e o resultado agregado, requisitos gerais acionáveis e o PDF original. A re-identificação SHALL ocorrer apenas na renderização para papel autorizado; nenhum artefato re-identificado é persistido nem enviado a qualquer LLM. A ordem dos cards SHALL seguir a leitura clínica: identificação, «Procedimentos do caso», sumário clínico e estrutura extraída (o quadro clínico) ANTES dos alertas consultivos e demais cards da automação. O detalhe NÃO exibe a trilha de eventos (vive no painel). O card «Procedimentos do caso» exibe TODAS as rows — inclusive a detectada não-declarada que sobrevive ao bypass da divergência — com badges de origem (Declarado/Detectado na extração) e de detecção após a reconciliação.
#### Scenario: Médico vê o sumário re-identificado

- **GIVEN** um caso em `AWAITING_DOCTOR` cujo `summary_text` contém o token `<PESSOA_1>`
- **WHEN** o médico autorizado abre o detalhe
- **THEN** o sumário é renderizado com o nome real do paciente no lugar do token

#### Scenario: Requisitos gerais acionáveis exibidos conforme alertas

- **GIVEN** um caso cujo policy gerou alerta de anticoagulante com protocolo de suspensão por fármaco
- **WHEN** o médico abre o detalhe
- **THEN** o card do procedimento exibe o requisito geral com o protocolo acionável correspondente ao alerta

#### Scenario: Recomendação consultiva visível por procedimento

- **GIVEN** um caso com `policy_result` recomendando recusar um procedimento com motivos e a sugestão agregada de recusa
- **WHEN** o médico abre o detalhe
- **THEN** os motivos da recusa por procedimento e o agregado são exibidos como alerta consultivo, sem bloquear a decisão

#### Scenario: Demografia do paciente no card de identificação

- **GIVEN** um caso com `patient_age`=84, `patient_gender`=`F` e `patient_race`=`Parda` extraídos do cabeçalho SESAB
- **WHEN** o médico autorizado abre o detalhe
- **THEN** o card de identificação exibe idade (84 a), sexo (F) e raça/cor (Parda) ao lado de nome e nº de ocorrência

#### Scenario: Demografia ausente mostra o placeholder padrão

- **GIVEN** um caso sem cabeçalho SESAB (campos de demografia vazios)
- **WHEN** o médico autorizado abre o detalhe
- **THEN** as linhas de idade, sexo e raça/cor exibem «—» como os demais campos ausentes do card, sem erro

#### Scenario: Quadro clínico antes do consultivo

- **GIVEN** um caso em `AWAITING_DOCTOR` com procedimentos declarados, sumário clínico, estrutura extraída e alertas consultivos
- **WHEN** o médico abre o detalhe
- **THEN** os títulos aparecem na ordem: Procedimentos do caso → Sumário clínico → Estrutura extraída → Alertas consultivos (o quadro clínico precede o consultivo da automação)

#### Scenario: Detalhe em decisão sem trilha de eventos

- **GIVEN** um caso em `AWAITING_DOCTOR` com trilha de eventos
- **WHEN** o médico abre o detalhe
- **THEN** nenhum bloco de trilha de eventos é renderizado

#### Scenario: Detalhe médico exibe row detectada não-declarada

- **GIVEN** um caso em `AWAITING_DOCTOR` cuja divergência foi liberada por bypass (declarada `angio_art_perif` não-detectada + `art_perif` detectada não-declarada persistida)
- **WHEN** o médico com especialidade compatível abre o detalhe
- **THEN** o card «Procedimentos do caso» exibe ambas as rows com rótulos legíveis e os badges «Não detectado» e «Detectado na extração»

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

O sistema SHALL exibir, para `doctor`/`admin` com acesso ao caso (regra de subtipo), o detalhe read-only de casos decididos com as decisões por procedimento, motivos, ator e data — **sem a trilha de eventos** (vive no painel) — e sem permitir nova decisão fora de `AWAITING_DOCTOR`.

#### Scenario: Detalhe decidido read-only

- **GIVEN** um caso em `DOCTOR_DENIED`
- **WHEN** o médico autorizado abre o detalhe
- **THEN** as decisões e motivos são exibidos, nenhum formulário de decisão é renderizado e nenhum bloco de trilha de eventos aparece
