# account-access Specification

## Purpose

Gestão de usuários e controle de acesso do HMD: papéis fixos do sistema, papel ativo único por sessão, proteção de views por papel, restrição de intranet para o papel `nir`, autenticação local transitória até a integração com o Active Directory e provisionamento administrativo inicial.

## Requirements

### Requirement: Papéis fixos do sistema

O sistema SHALL definir exatamente cinco papéis de usuário: `nir`, `doctor`, `scheduler`, `manager` e `admin`. O comando de seed administrativo SHALL criar os cinco papéis idempotentemente e um superusuário multi-role a partir de variáveis de ambiente.

#### Scenario: Seed idempotente

- **GIVEN** um banco vazio
- **WHEN** o comando de seed administrativo é executado duas vezes
- **THEN** os cinco papéis existem uma única vez e o superusuário não é duplicado

### Requirement: Usuário multi-role com papel ativo único em sessão

Um usuário pode possuir múltiplos papéis, mas a sessão SHALL manter no máximo um papel ativo por vez. Após o login, usuários com exatamente um papel têm o papel ativo definido automaticamente; usuários com mais de um papel são direcionados à tela de seleção de papel antes de acessar qualquer área protegida. A troca de papel ativo fica disponível em qualquer momento pela interface.

#### Scenario: Papel único definido automaticamente

- **GIVEN** um usuário ativo com apenas o papel `doctor`
- **WHEN** o usuário faz login
- **THEN** o papel ativo da sessão é `doctor` sem tela de seleção

#### Scenario: Multi-role exige seleção

- **GIVEN** um usuário ativo com os papéis `doctor` e `manager` e sem papel ativo na sessão
- **WHEN** o usuário tenta acessar qualquer view protegida
- **THEN** é redirecionado para a tela de seleção de papel

#### Scenario: Troca de papel atualiza a sessão

- **GIVEN** um usuário autenticado com papel ativo `doctor`
- **WHEN** troca o papel ativo para `manager` pela interface
- **THEN** a sessão passa a indicar `manager` como papel ativo e o usuário é levado à área do novo papel

### Requirement: Views protegidas exigem papel ativo correspondente

Views marcadas com a proteção por papel SHALL negar acesso (HTTP 403) a usuários cujo papel ativo não corresponde ao exigido, ainda que possuam o papel entre os seus.

#### Scenario: Papel ativo errado é negado

- **GIVEN** um usuário com papéis `doctor` e `manager` e papel ativo `doctor`
- **WHEN** acessa uma view que exige papel ativo `manager`
- **THEN** recebe HTTP 403

### Requirement: NIR restrito à rede interna

O acesso de usuários com papel ativo `nir` SHALL ser bloqueado quando o IP de origem estiver fora da faixa de intranet configurada. Os demais papéis podem acessar de qualquer rede. Os caminhos de autenticação (login, logout, seleção de papel) são isentos da restrição. Quando o middleware usa proxy reverso, o IP de origem SHALL ser lido do cabeçalho confiável configurado.

#### Scenario: NIR fora da faixa é bloqueado

- **GIVEN** um usuário com papel ativo `nir` e uma requisição originada de IP fora da faixa configurada
- **WHEN** acessa qualquer view não isenta
- **THEN** recebe resposta de acesso bloqueado por restrição de rede

#### Scenario: Doctor fora da faixa acessa normalmente

- **GIVEN** um usuário com papel ativo `doctor` e uma requisição originada de IP fora da faixa configurada
- **WHEN** acessa uma view protegida qualquer
- **THEN** a requisição prossegue normalmente

#### Scenario: Multi-role bloqueado como NIR pode trocar de papel de fora da rede

- **GIVEN** um usuário cujo conjunto de papéis contém `nir` e `manager`, com papel ativo `nir`, acessando de fora da faixa
- **THEN** o acesso é bloqueado enquanto o papel ativo for `nir`
- **WHEN** o usuário troca o papel ativo para `manager` pela tela de seleção (path isento da restrição)
- **THEN** o acesso externo passa a ser permitido

### Requirement: Autenticação local transitória

Durante o bootstrap, a autenticação SHALL usar credenciais locais (nome de usuário + senha hasheada no banco). Usuários com conta bloqueada ou removida não podem autenticar. Este mecanismo é transitório: o change `ad-kerberos-authentication` substituirá a autenticação de usuários comuns por Kerberos/AD, mantendo apenas um superusuário local de emergência.

#### Scenario: Login com credenciais válidas

- **GIVEN** um usuário ativo com senha local válida e ao menos um papel
- **WHEN** submete login com credenciais corretas
- **THEN** a sessão é criada e o fluxo de papel ativo é iniciado

#### Scenario: Conta bloqueada não autentica

- **GIVEN** um usuário com status de conta bloqueado
- **WHEN** submete login com credenciais corretas
- **THEN** o login é negado

### Requirement: Registro de conselho profissional opcional e consistente

O usuário pode registrar conselho profissional (`CRM` ou `COREN`) e número. Os dois campos SHALL ser preenchidos juntos ou deixados vazios juntos; combinação parcial é rejeitada na validação do modelo.

#### Scenario: Conselho sem número é rejeitado

- **GIVEN** um usuário preenchido com conselho `CRM` e sem número
- **WHEN** a validação do modelo é executada
- **THEN** a validação falha com erro explícito
