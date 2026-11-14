# Delta: ui-shell

## ADDED Requirements

### Requirement: Identidade reservada SAR–HMD

O sistema SHALL ter como default `SAR – HMD` (família SAR, unidade
HMD) em todas as superfícies: título do header, `<title>`, manual e
nome do manifest. Override por ambiente permanece suportado. Os três
tokens reservados — (1) primária `#0d4a8a` (inclui o `theme-color`
que a espelha), (2) `short_name "HMD"`, (3) sigla `HMD` no ícone —
SHALL permanecer os diferenciadores do HMD: nenhum alinhamento visual
futuro SHALL alterar esses três tokens.

#### Scenario: Marca visível em toda página

- **GIVEN** qualquer página autenticada ou pública
- **WHEN** o HTML é renderizado sem override de ambiente
- **THEN** o header exibe o ícone HMD e o texto `SAR – HMD`, e o
  `<title>` contém `SAR – HMD`

#### Scenario: Override de ambiente preservado

- **GIVEN** `APP_DISPLAY_NAME` definido no ambiente
- **WHEN** o HTML é renderizado
- **THEN** o valor do ambiente é exibido no lugar do default

#### Scenario: Tokens reservados intactos

- **GIVEN** o CSS e o manifest servidos
- **WHEN** inspecionados
- **THEN** a primária é `#0d4a8a`, o `short_name` é `HMD` e o
  `theme-color` é `#0d4a8a`

### Requirement: Header com faixa de navegação por papel

O sistema SHALL renderizar no layout base um header com a marca
(ícone + título), a sessão (`Nome · Papel`, avatar, sino, manual,
troca de papel, sair, toggler mobile) e, abaixo, uma faixa de
navegação por papel ativo com a aba corrente destacada e contadores
quando aplicável. Páginas SHALL declarar sua aba via bloco de
navegação; sem bloco, só marca e sessão aparecem.

#### Scenario: Faixa reflete o papel ativo

- **GIVEN** usuário com papel ativo `nir`
- **WHEN** qualquer página do NIR é renderizada
- **THEN** a faixa exibe as abas do NIR com a corrente destacada

#### Scenario: Sessão no padrão Nome-Papel

- **GIVEN** usuário autenticado `Maria Silva` com papel ativo médico
- **WHEN** o header é renderizado
- **THEN** exibe `Maria Silva · médico` com avatar, sino, manual,
  troca de papel e sair

### Requirement: Padrão de título de página

Toda página de trabalho SHALL exibir exatamente um `h1` com a classe
de título de página contendo o nome da visão (ex. `Pendentes`,
`Novo Encaminhamento`), distinto do `<title>` (que leva a marca).

#### Scenario: Título único por página

- **GIVEN** a fila médica aberta
- **WHEN** o HTML é renderizado
- **THEN** existe um `h1` de título com o nome da visão

### Requirement: Fundação visual e interações transversais

O sistema SHALL renderizar texto e títulos nas fontes do tema
hospitalar, com tokens de cor, componentes (botões, badges, status) e
controles de ação com altura mínima de 44px — primária HMD preservada.
Cada sino SHALL refletir uma nova notificação em até 60 segundos sem
recarregar a página (polling de 45 s com backoff até 5 min e pausa em
aba oculta, mesmo padrão do ats-web).

#### Scenario: Sino atualiza sem navegação

- **GIVEN** página autenticada aberta e uma nova notificação criada
- **WHEN** decorrem até 60 segundos
- **THEN** a contagem do sino reflete o novo total sem reload

#### Scenario: Abas usam os termos do ats-web

- **GIVEN** usuário navegando nas filas do NIR, médico e agendador
- **WHEN** as abas são exibidas
- **THEN** os rótulos seguem o vocabulário do ats-web (`Pendentes`,
  `Decididos`, `Novo Encaminhamento`, `Meus Casos`,
  `Casos Encerrados`)
