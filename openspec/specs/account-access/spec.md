# account-access Specification

## Purpose

Gestão de usuários e controle de acesso do HMD: papéis fixos do sistema, papel ativo único por sessão, proteção de views por papel, restrição de intranet para o papel `nir`, autenticação via Active Directory (Kerberos) com o admin como identidade local permanente (ADR-0009) e provisionamento administrativo.

## Requirements

### Requirement: Papéis fixos do sistema

O sistema SHALL definir exatamente cinco papéis de usuário: `nir`, `doctor`, `scheduler`, `manager` e `admin`. O comando de seed administrativo SHALL criar os cinco papéis idempotentemente e um superusuário multi-role a partir de variáveis de ambiente.

#### Scenario: Seed idempotente

- **GIVEN** um banco vazio
- **WHEN** o comando de seed administrativo é executado duas vezes
- **THEN** os cinco papéis existem uma única vez e o superusuário não é duplicado

### Requirement: Usuário multi-role com papel ativo único em sessão

Um usuário pode possuir múltiplos papéis, mas a sessão SHALL manter no máximo um papel ativo por vez. Após o login, usuários com exatamente um papel têm o papel ativo definido automaticamente; usuários com mais de um papel são direcionados à tela de seleção de papel antes de acessar qualquer área protegida. A troca de papel ativo fica disponível em qualquer momento pela interface. A tela de seleção de papel, quando acessada por usuário sem nenhum papel atribuído, SHALL encaminhar ao encerramento da sessão com mensagem explicativa.

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

#### Scenario: Seleção de papel com zero papéis encaminha ao logout

- **GIVEN** um usuário autenticado sem nenhum papel atribuído acessando diretamente a tela de seleção de papel
- **WHEN** a tela é processada
- **THEN** a sessão é encerrada com mensagem explicativa em vez de exibir lista vazia de papéis

### Requirement: Views protegidas exigem papel ativo correspondente

Views marcadas com a proteção por papel SHALL negar acesso (HTTP 403) a usuários cujo papel ativo não corresponde ao exigido, ainda que possuam o papel entre os seus.

#### Scenario: Papel ativo errado é negado

- **GIVEN** um usuário com papéis `doctor` e `manager` e papel ativo `doctor`
- **WHEN** acessa uma view que exige papel ativo `manager`
- **THEN** recebe HTTP 403

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

### Requirement: Autenticação por Active Directory com admin local por design

Usuários provisionados com `ad_upn` (UPN completo `cpf@dominio`) SHALL autenticar exclusivamente via Kerberos contra o Active Directory (CPF no login + senha do AD; realm derivado do sufixo do UPN); credenciais locais SHALL ser inutilizáveis para esses usuários. A checagem de `account_status` precede qualquer consulta ao KDC: contas com status diferente de `active` não autenticam e não geram tráfego Kerberos. A autenticação local é permitida **apenas para superusuários sem `ad_upn`**, em qualquer ambiente, **por design** (identidade administrativa permanente — ADR-0009; o admin do hospital usa a credencial do AD exclusivamente para atividades assistenciais e não possui segunda identidade no AD). Não existe mais flag de habilitação de autenticação local; preencher `ad_upn` no superusuário o torna autenticável por AD. Respostas ao usuário são genéricas, distinguindo apenas "credenciais inválidas" de "serviço de autenticação indisponível".

#### Scenario: Login com credenciais válidas

- **GIVEN** um usuário ativo provisionado com `ad_upn` completo (cpf@dominio), com papel atribuído, e a senha correta do AD
- **WHEN** submete login com CPF e senha
- **THEN** a sessão é criada e o fluxo de papel ativo é iniciado, com o realm derivado do sufixo do UPN

#### Scenario: Senha AD incorreta não autentica e não faz failover

- **GIVEN** um usuário provisionado com `ad_upn` e a senha errada
- **WHEN** submete login
- **THEN** o login é negado com mensagem genérica de credenciais inválidas e nenhum segundo DC é consultado para esta tentativa

#### Scenario: Conta bloqueada não autentica

- **GIVEN** um usuário com `account_status` bloqueado e senha correta do AD
- **WHEN** submete login
- **THEN** o login é negado sem que o KDC seja consultado

#### Scenario: Usuário AD não autentica com senha local

- **GIVEN** um usuário provisionado com `ad_upn`
- **WHEN** o backend local é consultado com qualquer senha local
- **THEN** a autenticação local é recusada para esse usuário

#### Scenario: Admin local autentica em qualquer ambiente

- **GIVEN** um superusuário sem `ad_upn`, com senha local válida, em ambiente de produção sem nenhuma configuração extra
- **WHEN** submete login (página comum ou Django admin)
- **THEN** a sessão é criada normalmente

#### Scenario: Usuário comum sem ad_upn não usa autenticação local

- **GIVEN** um usuário não-superusuário sem `ad_upn`, com senha local correta
- **WHEN** submete login
- **THEN** o login é negado com mensagem genérica
### Requirement: Registro de conselho profissional opcional e consistente

O usuário pode registrar conselho profissional (`CRM` ou `COREN`) e número. Os dois campos SHALL ser preenchidos juntos ou deixados vazios juntos; combinação parcial é rejeitada na validação do modelo.

#### Scenario: Conselho sem número é rejeitado

- **GIVEN** um usuário preenchido com conselho `CRM` e sem número
- **WHEN** a validação do modelo é executada
- **THEN** a validação falha com erro explícito

### Requirement: Provisionamento administrativo com ad_upn

O cadastro administrativo de usuários do AD SHALL registrar o `ad_upn` completo (`cpf@dominio`; único, opcional — o ambiente é uma floresta com múltiplos domínios em trust, portanto o sufixo não é restrito a um domínio fixo) e deixar a senha local inutilizável para esses usuários. O login SHALL ser o CPF normalizado, correspondente ao `sAMAccountName`.

#### Scenario: Usuário AD provisionado sem senha local

- **GIVEN** um administrador cadastra um usuário com `ad_upn` completo (cpf@dominio) preenchido
- **WHEN** o usuário é consultado
- **THEN** ele não possui senha local utilizável e seu `ad_upn` é único no sistema

### Requirement: Backend Kerberos com failover de DCs

A validação Kerberos SHALL usar o principal derivado do `ad_upn` (realm do sufixo, em maiúsculas) e consultar os DCs configurados em ordem, avançando para o próximo DC **somente** em erro de transporte, timeout ou KDC indisponível — respostas UDP grandes (código `52`) são reprocessadas via TCP **no mesmo DC** antes de qualquer classificação. Códigos KDC de autenticação (`6`, `18`, `23`, `24`, `37`) são definitivos e não provocam nova tentativa. Quando todos os DCs estão inalcançáveis, o resultado é "serviço indisponível", nunca "credenciais inválidas". O resultado interno preserva o código KDC do protocolo para diagnóstico, sem depender de texto de exceção.

#### Scenario: Erro de transporte no primeiro DC tenta o segundo

- **GIVEN** o primeiro DC configurado inalcançável (timeout/recusa de conexão)
- **WHEN** a validação Kerberos é executada
- **THEN** o segundo DC é consultado e o resultado reflete a resposta dele

#### Scenario: Falha de autenticação é definitiva

- **GIVEN** o primeiro DC responde com código KDC `24` (preauth falhou)
- **WHEN** a validação Kerberos é executada
- **THEN** nenhum outro DC é consultado para esta tentativa

#### Scenario: Todos os DCs inalcançáveis

- **GIVEN** todos os DCs configurados inalcançáveis
- **WHEN** o login é submetido
- **THEN** o usuário recebe mensagem genérica de serviço indisponível, distinta de credenciais inválidas

### Requirement: Proteção anti-lockout local

O sistema SHALL limitar tentativas de login malsucedidas por CPF normalizado e por par IP+CPF (IP de origem lido do cabeçalho confiável, mesma regra do guard de intranet), com limiar configurável inferior à política de lockout do AD. Tentativas que falham por **indisponibilidade do serviço de autenticação** (KDCs inalcançáveis) não geram tráfego ao AD e, portanto, SHALL não ser registradas como falha nos contadores. A proteção SHALL aplicar-se também ao login do Django admin: tentativas bloqueadas são recusadas cedo (sem tentativa de autenticação) e falhas de credencial local do perfil administrativo (superusuário sem `ad_upn`) contam para os mesmos contadores; tentativas de outros perfis nessa rota que falhem por indisponibilidade do AD não contam. Ao atingir o limiar dentro da janela, tentativas subsequentes são recusadas temporariamente com mensagem genérica, sem consultar o KDC e sem alterar `account_status`. Tentativa bem-sucedida reinicia os contadores. Em produção, o rate-limit SHALL operar sobre cache compartilhado entre processos.

#### Scenario: Limiar atingido bloqueia temporariamente

- **GIVEN** um CPF com tentativas malsucedidas suficientes para atingir o limiar dentro da janela
- **WHEN** uma nova tentativa de login é submetida
- **THEN** ela é recusada com mensagem genérica sem consultar o KDC

#### Scenario: Login bem-sucedido reinicia contadores

- **GIVEN** um CPF com falhas abaixo do limiar
- **WHEN** o login é bem-sucedido
- **THEN** os contadores de tentativas desse CPF são zerados

#### Scenario: Formatação do CPF não contorna o limite

- **GIVEN** tentativas malsucedidas com o mesmo CPF em variações de espaçamento ou caixa
- **WHEN** uma nova tentativa desse CPF é submetida
- **THEN** todas contam para o mesmo contador

#### Scenario: Falha por indisponibilidade não conta para o limite

- **GIVEN** tentativas de login que falham porque o serviço de autenticação está indisponível (KDCs inalcançáveis), em quantidade acima do limiar
- **WHEN** o serviço volta e o usuário submete login com credenciais corretas
- **THEN** o login é bem-sucedido sem bloqueio local residual

#### Scenario: Força-bruta no Django admin é limitada

- **GIVEN** um superusuário sem `ad_upn` e tentativas de senha incorreta no login do Django admin em quantidade suficiente para atingir o limiar
- **WHEN** uma nova tentativa é submetida no Django admin
- **THEN** ela é recusada cedo com mensagem genérica, sem chegar à checagem de senha

#### Scenario: Falha de AD no Django admin não conta para o limite

- **GIVEN** um usuário com `ad_upn` (e `is_staff`) cujas tentativas no login do Django admin falham por indisponibilidade do AD
- **WHEN** o serviço volta e ele submete login correto na página comum
- **THEN** o login é bem-sucedido sem bloqueio local residual
