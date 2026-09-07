# case-management Specification (delta)

## Purpose

Núcleo de domínio de casos do HMD: catálogo de 13 tipos de procedimento com perfis de exame, ciclo de vida de caso governado por máquina de estados de 17 estados com transições protegidas, procedimentos por caso neutros, trilha de auditoria append-only, locks de concorrência e comunicações operacionais — a mecânica consumida por todos os fluxos posteriores.

## ADDED Requirements

### Requirement: Catálogo de 13 tipos de procedimento

O sistema SHALL definir, em código (registro de perfis), exatamente os 13 tipos de procedimento do HMD, cada um com nome canônico, rótulo de exibição, seção de critérios clínicos (S1–S8), subtipo de doctor (angio/neuro/cardio/radio) e indicação de suporte anestésico. Existir no catálogo equivale a estar habilitado (sem flag separada). A tabela de thresholds por seção SHALL estar disponível como dados consultáveis. Novos tipos são adicionados exclusivamente por mudança de código (change explícito), sem migração de dados.

#### Scenario: Catálogo completo e consultável

- **GIVEN** o catálogo de perfis carregado
- **WHEN** os 13 tipos são consultados
- **THEN** cada um retorna label, seção de critérios, subtipo doctor e flag de suporte anestésico coerentes com o documento de parâmetros clínicos (flebografia usa a seção 1; angio_carotidas usa a seção 2)

#### Scenario: Tipo desconhecido é rejeitado

- **GIVEN** um identificador de tipo que não consta no catálogo
- **WHEN** é usado para criar um procedimento de caso
- **THEN** a operação é rejeitada com erro explícito

#### Scenario: Seed de verificação idempotente

- **GIVEN** o banco do HMD
- **WHEN** o comando de seed do catálogo é executado duas vezes
- **THEN** a segunda execução não duplica nada e reporta sucesso

### Requirement: FSM de 17 estados com transições protegidas

O ciclo de vida de um caso SHALL ser governado por uma máquina de estados com exatamente os 17 estados `NEW, PDF_EXTRACTING, ANONYMIZING, LLM_EXTRACTING, LLM_SUMMARIZING, AWAITING_DOCTOR, DOCTOR_DENIED, DOCTOR_ACCEPTED, SCHEDULER_REQUESTED, AWAITING_SCHEDULING, SCHEDULING_CONFIRMED, SCHEDULING_DENIED, FAILED, FINAL_REPLY_POSTED, AWAITING_NIR_ACK, CLEANING, CLEANED`. Transições só ocorrem pelas operações definidas; uma tentativa de transição inválida a partir do estado atual SHALL ser rejeitada com erro explícito, sem alterar o caso. Estados de processamento (extração/anonimização/LLM) SHALL ter caminho para `FAILED`. O conjunto de estados é contrato: mudanças posteriores SHALL adicionar transições, não estados.

#### Scenario: Caminho feliz até a fila médica

- **GIVEN** um caso recém-criado em `NEW`
- **WHEN** as operações de processamento são executadas em sequência (extração de PDF, anonimização, extração LLM, sumarização LLM)
- **THEN** o caso atravessa `PDF_EXTRACTING → ANONYMIZING → LLM_EXTRACTING → LLM_SUMMARIZING` e chega a `AWAITING_DOCTOR`

#### Scenario: Negação médica vai direto à resposta final

- **GIVEN** um caso em `AWAITING_DOCTOR`
- **WHEN** a decisão médica registra negação de todos os procedimentos
- **THEN** o caso transita por `DOCTOR_DENIED` e chega a `FINAL_REPLY_POSTED` sem passar pela fila de agendamento

#### Scenario: Aceitação médica segue para agendamento

- **GIVEN** um caso em `AWAITING_DOCTOR` com ao menos um procedimento aceito
- **WHEN** a decisão médica é registrada
- **THEN** o caso transita por `DOCTOR_ACCEPTED` para `SCHEDULER_REQUESTED` e depois `AWAITING_SCHEDULING`

#### Scenario: Transição inválida é rejeitada

- **GIVEN** um caso em `AWAITING_DOCTOR`
- **WHEN** é executada uma operação de transição que não parte de `AWAITING_DOCTOR` (ex.: confirmação de agendamento)
- **THEN** a operação falha com erro explícito e o estado do caso permanece `AWAITING_DOCTOR`

#### Scenario: Falha de processamento leva a FAILED

- **GIVEN** um caso em um estado de processamento (ex.: `ANONYMIZING`)
- **WHEN** o passo correspondente falha
- **THEN** o caso transita para `FAILED` e o motivo fica registrado na trilha de auditoria

### Requirement: Trilha de auditoria append-only por caso

Cada transição de estado e cada operação de procedimento SHALL gravar um evento append-only no caso, com timestamp, ator (usuário ou sistema) e papel ativo do ator no momento, tipo de evento e payload enxuto. Eventos não podem ser alterados ou removidos pelas operações de negócio; a trilha é a fonte de verdade da história do caso.

#### Scenario: Transição grava evento com ator e papel

- **GIVEN** um caso em `NEW` e um usuário autenticado com papel ativo `nir`
- **WHEN** o caso avança de estado
- **THEN** existe exatamente um evento novo para a transição, com o usuário como ator, o papel ativo `nir`, e payload descrevendo a transição (origem, destino)

#### Scenario: Operação de procedimento grava evento

- **GIVEN** um caso em processamento
- **WHEN** a detecção de procedimentos é registrada
- **THEN** um evento é gravado com o resultado da detecção (tipos detectados/não detectados) no payload

### Requirement: Procedimentos por caso neutros e atômicos

A dimensão de procedimento SHALL viver exclusivamente em rows de `CaseProcedure` vinculadas ao caso (um caso pode ter 1–N procedimentos), com no máximo uma row por (caso, tipo). A declaração de tipos pelo NIR, a detecção pelo pipeline e a decisão médica por procedimento SHALL ser operações atômicas que substituem o estado anterior coerente e gravam evento; nenhuma dessas operações perde rows órfãs nem cria duplicatas.

#### Scenario: Declaração atômica substitui a anterior

- **GIVEN** um caso com declaração anterior de `art_perif`
- **WHEN** o NIR declara `art_perif` e `nefrostomia`
- **THEN** o caso passa a ter exatamente duas rows declaradas, sem duplicatas e sem rows de tipos não declarados

#### Scenario: Declaração com tipo inválido falha inteira

- **GIVEN** um caso sem declaração
- **WHEN** o NIR declara um conjunto contendo um tipo fora do catálogo
- **THEN** nenhuma row é criada e o erro identifica o tipo inválido

#### Scenario: Decisão por procedimento é registrada por row

- **GIVEN** um caso com dois procedimentos declarados
- **WHEN** o médico decide aceitar um e negar o outro com motivo
- **THEN** cada row carrega sua própria decisão e motivo, e um evento resume as decisões

### Requirement: Locks de concorrência por caso

O sistema SHALL suportar lock/lease por caso: um ator (com contexto e papel) pode reivindicar exclusividade de mutação; um segundo claim conflitante sobre caso travado e não expirado SHALL ser negado com erro explícito; **fluxos que operam sob lock SHALL exigir a posse do token para mutar o caso** (`assert`; os consumers ligam a checagem nas mutações dos seus fluxos em changes posteriores — o mecanismo é o contrato deste change); locks expiram por tempo (lease) e leases expiradas podem ser assumidas por novo claim; release devolve o caso. Workers e papéis usam o mesmo mecanismo.

#### Scenario: Claim concede exclusividade

- **GIVEN** um caso sem lock
- **WHEN** um doctor faz claim com lease
- **THEN** o claim é concedido com token válido até o fim da lease

#### Scenario: Claim conflitante é negado

- **GIVEN** um caso com lock ativo de um doctor (lease não expirada)
- **WHEN** outro ator tenta claim
- **THEN** o claim é negado com erro explícito e o lock original permanece

#### Scenario: Lease expirada pode ser assumida

- **GIVEN** um caso com lock cuja lease expirou
- **WHEN** outro ator faz claim
- **THEN** o claim é concedido com novo token e o evento de expiração fica na trilha

### Requirement: Comunicações operacionais por caso

Cada caso SHALL ter uma thread de comunicações append-only com dois tipos de mensagem: `user` (manual, com autor e papel ativo no momento do post) e `system` (automática, sem autor, projetada a partir de eventos relevantes da FSM). Mensagens `system` não geram notificação nem estado de leitura.

#### Scenario: Mensagem de usuário registra autor e papel

- **GIVEN** um usuário autenticado com papel ativo `scheduler`
- **WHEN** posta uma mensagem no caso
- **THEN** a mensagem é gravada com autor, papel ativo `scheduler` e timestamp, e aparece na thread em ordem

#### Scenario: Evento FSM projeta mensagem sistêmica

- **GIVEN** um caso cuja transição é de um tipo que exige comunicação sistêmica (ex.: chegada à fila médica)
- **WHEN** a transição ocorre
- **THEN** uma mensagem `system` é criada vinculada ao evento, sem autor e sem notificação
