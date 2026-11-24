# Spec Delta

## ADDED Requirements

### Requirement: Perfil sem troca de senha

A página de perfil SHALL exibir apenas os dados da conta e a orientação de que a troca de senha é feita pessoalmente na TI do hospital. O perfil SHALL conter campos de senha, formulários, botões ou links de troca de senha em nenhum papel. Requisições de escrita ao perfil SHALL ser recusadas sem alterar credenciais nem a sessão.

#### Scenario: Perfil exibe dados e orientação, sem formulário de senha

- **GIVEN** um usuário autenticado com papel ativo `doctor`
- **WHEN** acessa o perfil
- **THEN** a página exibe usuário, nome e papéis com rótulos, exibe a orientação para procurar a TI do hospital e não contém campo de senha, formulário de troca de senha nem o rótulo «Alterar senha»

#### Scenario: Escrita no perfil é recusada

- **GIVEN** um usuário autenticado que submete `POST` ao perfil com a senha atual e uma nova senha
- **WHEN** a requisição é processada
- **THEN** a resposta é 405, a senha do usuário não é alterada e a sessão permanece válida

#### Scenario: Perfil continua exigindo login

- **GIVEN** um usuário anônimo
- **WHEN** acessa o perfil
- **THEN** é redirecionado ao login

### Requirement: Sem rotas de troca de senha no aplicativo

O aplicativo SHALL expor rota de troca ou redefinição de senha em nenhum lugar — nem autoatendimento, nem para outro usuário, inclusive no Django admin. Requisição autenticada a esses caminhos SHALL não exibir formulário de senha nem alterar credenciais: as de autoatendimento terminam em 404; a por usuário redireciona ao índice do admin. A troca da senha do AD SHALL ser presencial na TI do hospital; a do admin local (ADR-0009) SHALL ser rotacionada pela TI fora do aplicativo.

#### Scenario: Rota de autoatendimento do admin não existe

- **GIVEN** um superusuário autenticado no Django admin
- **WHEN** requisita `/admin/password_change/` ou `/admin/password_change/done/` (leitura ou `POST` com senhas)
- **THEN** recebe 404 e a senha não é alterada

#### Scenario: Rota de senha de outro usuário no admin não existe

- **GIVEN** um superusuário autenticado no Django admin e um usuário provisionado com `ad_upn`
- **WHEN** requisita a rota de senha desse usuário com `POST` de nova senha
- **THEN** nenhuma página de senha é exibida, a navegação é redirecionada ao índice do Django admin com aviso de objeto inexistente e o usuário permanece sem senha local utilizável

#### Scenario: Nenhuma rota de senha registrada

- **GIVEN** a configuração de URLs do projeto
- **WHEN** todos os padrões de URL são inspecionados
- **THEN** nenhum caminho ou nome de rota contém `password` em sua composição

#### Scenario: Páginas do admin sem link de troca de senha

- **GIVEN** o superusuário local autenticado no Django admin
- **WHEN** qualquer página do admin é renderizada, inclusive o formulário de edição de usuário
- **THEN** a página é renderizada sem erro e não contém link, botão nem instrução para trocar ou redefinir senha — exceto os campos de senha do formulário de criação de usuário, preservados por decisão do dono (provisionamento da identidade local)

#### Scenario: Páginas administrativas preservadas

- **GIVEN** o superusuário local autenticado no Django admin
- **WHEN** navega entre as páginas de usuários e papéis
- **THEN** todas respondem normalmente, sem erro de resolução de URL
