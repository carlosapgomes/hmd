# Delta: account-access

## MODIFIED Requirements

### Requirement: Rótulos de papel em português na interface

As chaves internas de papel (`doctor`, `scheduler`, `manager`, `nir`, `admin`)
SHALL permanecer os identificadores de dados, sessão e permissionamento. A
exibição ao usuário nas superfícies de interface SHALL usar rótulos em
português a partir de uma fonte única: `doctor` exibe "médico", `scheduler`
exibe "agendador", `manager` exibe "supervisor"; `nir` e `admin` exibem
"nir"/"admin" conforme já estabelecido. Chaves de papel fora do mapeamento
(incluindo `system`) SHALL exibir a própria chave, sem quebrar a renderização.
O formulário de seleção de papel SHALL submeter a chave crua (não o rótulo).

Superfícies cobertas: badge de papel ativo (`base.html`), seleção de papel
(`accounts/switch_role.html`), lista de papéis da home da conta
(`accounts/home.html`) e do perfil (`accounts/profile.html`), e as menções a
papel nas comunicações dos detalhes de caso (`intake/case_detail.html`,
`scheduler/case_detail.html`) e na trilha de eventos do **detalhe do caso no
painel** (`dashboard/case_detail.html`) — os detalhes de NIR e médico não
exibem mais trilha (change `painel-ats-parity`; a trilha vive no painel).

#### Scenario: Badge de papel ativo mostra o rótulo

- **GIVEN** um usuário autenticado com papel ativo `doctor`
- **WHEN** qualquer página renderiza o badge de papel ativo
- **THEN** o badge exibe "médico" e não a chave `doctor`

#### Scenario: Seleção de papel lista rótulos e submete chaves

- **GIVEN** um usuário autenticado com os papéis `doctor` e `manager`
- **WHEN** a tela de seleção de papel é renderizada
- **THEN** os botões exibem "médico" e "supervisor" e cada formulário submete
  a chave crua correspondente (`doctor`, `manager`)

#### Scenario: Perfil lista rótulos

- **GIVEN** um usuário autenticado com os papéis `scheduler` e `manager`
- **WHEN** o perfil é renderizado
- **THEN** a lista de papéis exibe "agendador" e "supervisor"

#### Scenario: Home da conta lista rótulos para papel sem área própria

- **GIVEN** um usuário autenticado com papéis `nurse` e `doctor` e papel ativo
  `nurse` (fora da tabela de áreas de trabalho)
- **WHEN** a home da conta é renderizada
- **THEN** a lista de papéis exibe "médico" para `doctor` e a própria chave
  `nurse` para o papel fora do mapeamento (fallback D3)

#### Scenario: Papel desconhecido exibe a própria chave

- **GIVEN** uma trilha de eventos cujo `actor_role` é `system`
- **WHEN** o detalhe do caso é renderizado no painel
- **THEN** o papel do evento exibe `system`, sem erro

#### Scenario: Trilha e comunicações mostram o rótulo do papel

- **GIVEN** um caso com evento decidido por `doctor` e comunicação postada por
  `manager`
- **WHEN** o detalhe do caso é renderizado: trilha no painel, comunicações no NIR
- **THEN** a trilha do painel exibe "papel médico" e a comunicação do NIR exibe
  o badge "supervisor" (não as chaves)

#### Scenario: Troca de papel continua funcionando por chave

- **GIVEN** um usuário autenticado com os papéis `doctor` e `manager` e papel
  ativo `doctor`
- **WHEN** seleciona o botão rotulado "supervisor" na tela de seleção
- **THEN** a sessão passa a indicar `manager` como papel ativo
