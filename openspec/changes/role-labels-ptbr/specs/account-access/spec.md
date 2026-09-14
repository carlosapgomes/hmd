## ADDED Requirements

### Requirement: Rótulos de papel em português na interface

As chaves internas de papel (`doctor`, `scheduler`, `manager`, `nir`, `admin`)
SHALL permanecer os identificadores de dados, sessão e permissionamento. A
exibição ao usuário nas superfícies de interface SHALL usar rótulos em
português a partir de uma fonte única: `doctor` exibe "médico", `scheduler`
exibe "agendador", `manager` exibe "supervisor"; `nir` e `admin` exibem
"NIR"/"admin" conforme já estabelecido. Uma chave de papel desconhecida SHALL
exibir a própria chave, sem quebrar a renderização. O formulário de seleção de
papel SHALL submeter a chave crua (não o rótulo).

#### Scenario: Badge de papel ativo mostra o rótulo

- **GIVEN** um usuário autenticado com papel ativo `doctor`
- **WHEN** qualquer página renderiza o badge de papel ativo
- **THEN** o badge exibe "médico" e não a chave `doctor`

#### Scenario: Seleção de papel lista rótulos e submete chaves

- **GIVEN** um usuário autenticado com os papéis `doctor` e `manager`
- **WHEN** a tela de seleção de papel é renderizada
- **THEN** os botões exibem "médico" e "supervisor" e cada formulário submete
  a chave crua correspondente (`doctor`, `manager`)

#### Scenario: Home e perfil listam rótulos

- **GIVEN** um usuário autenticado com os papéis `scheduler` e `manager`
- **WHEN** a home da conta e o perfil são renderizados
- **THEN** as listas de papéis exibem "agendador" e "supervisor"

#### Scenario: Papel desconhecido exibe a própria chave

- **GIVEN** um usuário cujo papel ativo é uma chave fora do mapeamento
- **WHEN** o badge de papel ativo é renderizado
- **THEN** a própria chave é exibida, sem erro

#### Scenario: Troca de papel continua funcionando por chave

- **GIVEN** um usuário autenticado com papéis `doctor` e `manager` e papel
  ativo `doctor`
- **WHEN** seleciona o botão rotulado "supervisor" na tela de seleção
- **THEN** a sessão passa a indicar `manager` como papel ativo
