# account-access Specification (delta)

## MODIFIED Requirements

### Requirement: Acesso externo restrito a conjuntos exclusivamente restritos

O acesso externo (IP de origem fora da faixa de intranet configurada) SHALL ser bloqueado apenas para usuários cujo conjunto de papéis contém exclusivamente papéis listados em `INTRANET_RESTRICTED_ROLES` (ex.: um usuário apenas `nir`). Usuários com ao menos um papel fora desse conjunto SHALL acessar de qualquer rede, inclusive com papel ativo restrito, sem necessidade de trocar de papel. Os caminhos de autenticação (login, logout, seleção de papel) são isentos da restrição. Quando o middleware usa proxy reverso, o IP de origem SHALL ser lido do cabeçalho confiável configurado. Ao bloquear, o sistema SHALL encerrar a sessão do usuário e responder com página de bloqueio que ofereça retorno à tela de login.

#### Scenario: NIR puro fora da faixa é bloqueado

- **GIVEN** um usuário cujo conjunto de papéis é apenas `nir`, com papel ativo `nir`, e uma requisição originada de IP fora da faixa configurada
- **WHEN** acessa qualquer view não isenta
- **THEN** recebe resposta de acesso bloqueado por restrição de rede (403), sua sessão é encerrada e a página de bloqueio oferece retorno à tela de login

#### Scenario: Papel não restrito fora da faixa acessa normalmente

- **GIVEN** um usuário com papel ativo `doctor` e uma requisição originada de IP fora da faixa configurada
- **WHEN** acessa uma view protegida qualquer
- **THEN** a requisição prossegue normalmente

#### Scenario: Multi-role com papel ativo NIR acessa externamente

- **GIVEN** um usuário cujo conjunto de papéis contém `nir` e `manager`, com papel ativo `nir`, e uma requisição originada de IP fora da faixa configurada
- **WHEN** acessa uma view protegida qualquer
- **THEN** a requisição prossegue normalmente, sem necessidade de trocar o papel ativo

#### Scenario: Conjunto inteiro restrito mantém o bloqueio

- **GIVEN** um usuário cujo conjunto de papéis é `{nir, scheduler}`, com `INTRANET_RESTRICTED_ROLES = [nir, scheduler]`, com papel ativo `nir`, e uma requisição originada de IP fora da faixa configurada
- **WHEN** acessa uma view não isenta, inclusive após trocar o papel ativo para `scheduler`
- **THEN** o acesso permanece bloqueado por restrição de rede

#### Scenario: Usuário bloqueado consegue retornar ao login

- **GIVEN** um usuário exclusivamente restrito que acabou de ser bloqueado de fora da faixa (sessão encerrada)
- **WHEN** segue o retorno à tela de login oferecido pela página de bloqueio
- **THEN** o formulário de login é exibido (usuário anônimo), sem redirecionamento de volta à área bloqueada
