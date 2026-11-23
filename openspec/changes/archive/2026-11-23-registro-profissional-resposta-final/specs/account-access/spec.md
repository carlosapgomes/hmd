# Spec Delta

## MODIFIED Requirements

### Requirement: Registro de conselho profissional opcional e consistente

O usuário pode registrar conselho profissional (`CRM` ou `COREN`) e número. Os dois campos SHALL ser preenchidos juntos ou deixados vazios juntos; combinação parcial é rejeitada na validação do modelo. O cadastro administrativo de usuários (Django admin) SHALL expor os dois campos juntos numa seção própria, permitindo o provisionamento e a edição do par em qualquer momento.

#### Scenario: Conselho sem número é rejeitado

- **GIVEN** um usuário preenchido com conselho `CRM` e sem número
- **WHEN** a validação do modelo é executada
- **THEN** a validação falha com erro explícito

#### Scenario: Admin provisiona o par de conselho

- **GIVEN** um administrador edita um usuário no Django admin
- **WHEN** salva com conselho `CRM` e número preenchidos
- **THEN** o par é persistido e `professional_registration_display` devolve `CRM {número}`

#### Scenario: Admin deixa o par vazio

- **GIVEN** um administrador edita um usuário com o par preenchido
- **WHEN** salva com os dois campos vazios
- **THEN** o par é persistido vazio e o registro formatado devolve string vazia

#### Scenario: Parcial no admin é rejeitado

- **GIVEN** um administrador edita um usuário no Django admin
- **WHEN** salva com conselho preenchido e número vazio
- **THEN** o formulário do admin rejeita com erro apontando o campo pendente
