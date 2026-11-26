# Spec Delta

## ADDED Requirements

### Requirement: Lista de usuários exibe o nome completo

A lista de usuários do Django admin SHALL exibir uma coluna «Nome completo» com o nome e o sobrenome do usuário. Quando o nome está vazio, a coluna SHALL exibir o `username`. A coluna SHALL ser ordenável pelo nome, e a coluna do `username` SHALL permanecer a primeira coluna e o link de edição.

#### Scenario: Coluna exibe nome e sobrenome

- **GIVEN** um usuário cadastrado com nome e sobrenome preenchidos
- **WHEN** um administrador abre a lista de usuários do Django admin
- **THEN** a coluna «Nome completo» exibe o nome completo do usuário e a coluna do `username` continua sendo o link para o formulário de edição

#### Scenario: Usuário sem nome exibe o username

- **GIVEN** um usuário cadastrado sem nome e sobrenome
- **WHEN** um administrador abre a lista de usuários do Django admin
- **THEN** a coluna «Nome completo» exibe o `username` desse usuário

#### Scenario: Ordenação pelo nome

- **GIVEN** usuários com primeiros nomes diferentes
- **WHEN** um administrador ordena a lista pela coluna «Nome completo»
- **THEN** as linhas são ordenadas pelo primeiro nome

### Requirement: Busca da lista de usuários alcança nome e sobrenome

A busca da lista de usuários do Django admin SHALL casar, de forma parcial e sem diferenciar maiúsculas, com o nome e o sobrenome do usuário, além do `username` e do `ad_upn`.

#### Scenario: Busca pelo sobrenome

- **GIVEN** um usuário com sobrenome «Silva»
- **WHEN** um administrador busca por «sil» na lista de usuários
- **THEN** o usuário aparece no resultado

#### Scenario: Busca pelo nome não retorna usuário sem correspondência

- **GIVEN** usuários «Maria Silva» e «Carlos Souza»
- **WHEN** um administrador busca por «maria» na lista de usuários
- **THEN** somente «Maria Silva» aparece no resultado
