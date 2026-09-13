# account-access Specification (delta)

## REMOVED Requirements

### Requirement: NIR restrito à rede interna

Removido porque a regra mudou de eixo (decisão do dono pós-piloto,
2026-09-13): a restrição deixa de seguir o **papel ativo** e passa a seguir o
**conjunto de papéis** do usuário. O requisito substituto é "Acesso externo
restrito a conjuntos exclusivamente restritos", com os cenários reescritos
(o cenário da rota de fuga por troca de papel foi invertido).

## ADDED Requirements

### Requirement: Acesso externo restrito a conjuntos exclusivamente restritos

O acesso externo (IP de origem fora da faixa de intranet configurada) SHALL ser bloqueado apenas para usuários cujo conjunto de papéis contém exclusivamente papéis listados em `INTRANET_RESTRICTED_ROLES` (ex.: um usuário apenas `nir`). Usuários com ao menos um papel fora desse conjunto SHALL acessar de qualquer rede, inclusive com papel ativo restrito, sem necessidade de trocar de papel. Os caminhos de autenticação (login, logout, seleção de papel) são isentos da restrição. Quando o middleware usa proxy reverso, o IP de origem SHALL ser lido do cabeçalho confiável configurado.

#### Scenario: NIR puro fora da faixa é bloqueado

- **GIVEN** um usuário cujo conjunto de papéis é apenas `nir`, com papel ativo `nir`, e uma requisição originada de IP fora da faixa configurada
- **WHEN** acessa qualquer view não isenta
- **THEN** recebe resposta de acesso bloqueado por restrição de rede

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
