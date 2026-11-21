## Purpose

Intercorrência pós-agendamento em caso já encerrado: o NIR localiza um caso `CLEANED`
(por nº de ocorrência ou nome do paciente, qualquer criador), abre uma intercorrência com
motivo obrigatório e o caso reentra no fluxo de agendamento até novo encerramento —
paridade com o `open_post_acceptance_issue` do ats-web, adaptada ao FSM e à política de
disposição de dados do HMD.


## Requirements

### Requirement: Elegibilidade da intercorrência pós-encerramento

Um caso SHALL ser elegível à abertura de intercorrência pós-encerramento somente quando:
status `CLEANED`; possuir agendamento anterior confirmado na unidade 1 (com dados de
agendamento persistidos ou recuperáveis na trilha); possuir dados clínicos retidos (não
minimizados e não purgados); e não possuir intercorrência ativa. Caso inelegível, o
sistema SHALL exibir motivo legível correspondente (issue ativa, caso não encerrado, sem
agendamento confirmado, unidade 2, ou dados dispostos) sem oferecer a ação de abertura.

#### Scenario: Caso encerrado com agendamento confirmado é elegível

- **GIVEN** um caso `CLEANED` que passou por confirmação de agendamento na unidade 1, com dados retidos
- **WHEN** o NIR consulta a elegibilidade na busca de encerrados
- **THEN** o caso é listado como elegível com a ação de abertura de intercorrência

#### Scenario: Caso com dados minimizados é inelegível

- **GIVEN** um caso `CLEANED` cujos dados clínicos foram minimizados (flag de disposição ativa) ou purgados retroativamente
- **WHEN** o NIR consulta a elegibilidade
- **THEN** o caso não é elegível e o motivo exibido informa que os dados do caso foram dispostos

#### Scenario: Intercorrência ativa bloqueia nova abertura

- **GIVEN** um caso com intercorrência ativa (reaberto, ainda não re-encerrado)
- **WHEN** o NIR tenta abrir nova intercorrência
- **THEN** a abertura é recusada com motivo de issue já existente

#### Scenario: Caso não encerrado é inelegível

- **GIVEN** um caso em qualquer status anterior a `CLEANED`
- **WHEN** o NIR consulta a elegibilidade na busca de encerrados
- **THEN** o caso só é elegível pelas regras de intercorrência em andamento já existentes; a abertura pós-encerramento não é oferecida

#### Scenario: Agendamento anterior na unidade 2 é inelegível

- **GIVEN** um caso `CLEANED` cujo agendamento confirmado anterior era na unidade 2
- **WHEN** o NIR consulta a elegibilidade
- **THEN** o caso não é elegível e o motivo informa que a intercorrência é disponível apenas para a unidade 1

### Requirement: Abertura pelo NIR com motivo obrigatório

A abertura de intercorrência pós-encerramento SHALL ser um POST do papel `nir` sobre caso
elegível, com motivo obrigatório de texto livre (padrão do desmarcar — decisão do dono,
sem catálogo fechado e sem campo de mensagem separado), executado de forma atômica com
bloqueio do caso: transição de status `CLEANED` para `AWAITING_SCHEDULING`, limpeza dos
campos de agendamento corrente, persistência do motivo de reabertura, registro de evento
de auditoria dedicado cujo payload contém o motivo informado e o snapshot do agendamento
anterior (dados administrativos: unidade, data/hora, local — precedente vigente do evento
de reabertura), comunicação à thread do caso com o motivo interpolado e notificações pelos
marcos existentes (criador do caso e agendadores ativos). Abertura inválida (motivo
ausente/vazio, caso inelegível) SHALL falhar sem qualquer escrita.

#### Scenario: Abertura válida reabre o caso para o agendador

- **GIVEN** um caso elegível e o formulário com motivo válido preenchido
- **WHEN** o NIR submete a abertura
- **THEN** o caso passa a `AWAITING_SCHEDULING`, aparece na fila de pendentes do agendador, o motivo fica persistido e o evento de auditoria registra a reabertura com o snapshot do agendamento anterior

#### Scenario: Motivo ausente não escreve nada

- **GIVEN** o formulário de abertura sem motivo
- **WHEN** o NIR submete
- **THEN** a abertura é recusada com erro de validação e o caso permanece `CLEANED`, sem evento, comunicação ou notificação

#### Scenario: Corrida de elegibilidade é revalidada sob bloqueio

- **GIVEN** dois operadores abrindo intercorrência no mesmo caso simultaneamente
- **WHEN** o segundo POST é processado
- **THEN** ele falha com motivo de issue já existente (revalidação dentro do bloqueio atômico), sem escrita parcial

#### Scenario: Notificações usam os marcos existentes

- **GIVEN** a abertura válida de intercorrência
- **WHEN** a transação é confirmada
- **THEN** o criador do caso é notificado da reabertura e os agendadores ativos recebem a notificação de caso reaberto, reutilizando o conjunto fechado de marcos — nenhum marco novo com PHI

#### Scenario: Motivo e snapshot no evento de auditoria

- **GIVEN** a abertura válida com motivo preenchido
- **WHEN** o evento de auditoria é gravado e a trilha é expandida
- **THEN** o payload contém o motivo informado e o snapshot administrativo do agendamento anterior (unidade, data/hora, local); o motivo também aparece na comunicação da thread do caso

### Requirement: Ciclo de re-confirmação e novo encerramento

Um caso reaberto por intercorrência pós-encerramento SHALL seguir o fluxo de agendamento
existente: reserva, confirmação/negação com resposta final, ciência do NIR e novo
encerramento (`CLEANING` → `CLEANED`), com a intercorrência encerrada (sem issue ativa) ao
concluir o novo ciclo — a issue ativa SHALL ser zerada em QUALQUER chegada a `CLEANED`
(ciência ou encerramento administrativo), no mesmo atomic, com evento de auditoria. A
ciência do novo encerramento permanece restrita ao CRIADOR do caso (guarda vigente
preservada); indisponibilidade do criador é tratada pelo encerramento administrativo
existente (manager/admin). O histórico de agendamentos anteriores SHALL permanecer
consultável na trilha de eventos e o motivo da reabertura SHALL persistir no caso até
nova reabertura.

#### Scenario: Re-confirmação fecha o ciclo

- **GIVEN** um caso reaberto por intercorrência em `AWAITING_SCHEDULING`
- **WHEN** o agendador confirma o novo agendamento e o NIR registra a ciência da nova resposta final
- **THEN** o caso volta a `CLEANED` sem intercorrência ativa, elegível novamente a nova abertura, e a trilha exibe os dois ciclos de agendamento

#### Scenario: Caso reaberto aparece na busca de encerrados como issue ativa

- **GIVEN** um caso reaberto por intercorrência (fora de `CLEANED`)
- **WHEN** o NIR busca em casos encerrados pelo nº do caso
- **THEN** o caso aparece na busca como caso com intercorrência ativa (critério de inclusão da busca), sem ação de nova abertura

#### Scenario: Encerramento administrativo durante issue ativa zera a issue

- **GIVEN** um caso reaberto por intercorrência (issue ativa) sendo encerrado administrativamente
- **WHEN** o encerramento administrativo é concluído
- **THEN** o caso chega a `CLEANED` com a issue zerada no mesmo atomic e um evento de auditoria registra o encerramento da issue; a corrida abertura×encerramento é resolvida pelo bloqueio (revalidação sob `select_for_update`)

#### Scenario: Nova ciência permanece do criador

- **GIVEN** um caso reaberto por intercorrência cujo criador é outro usuário NIR
- **WHEN** o novo ciclo completa a resposta final
- **THEN** apenas o criador original pode registrar a ciência (guarda vigente); outro NIR recebe recusa e o caso permanece aguardando ciência

#### Scenario: Criador indisponível usa o encerramento administrativo

- **GIVEN** um caso reaberto com criador indisponível e resposta final publicada
- **WHEN** manager/admin aplica o encerramento administrativo existente
- **THEN** o caso encerra (issue zerada, evento registrado) sem afrouxar a guarda de ciência do criador

#### Scenario: Snapshot do agendamento anterior na trilha

- **GIVEN** um caso reaberto cujo agendamento anterior tinha unidade, data/hora e local
- **WHEN** o NIR expande a trilha de eventos
- **THEN** o evento de reabertura exibe rótulo legível com a categoria de auditoria e o snapshot do agendamento anterior, sem PHI
