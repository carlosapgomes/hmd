# dashboard Specification (delta)

## MODIFIED Requirements

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
