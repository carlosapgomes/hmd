# account-access Specification (delta)

## Purpose

Substituir a autenticação local transitória por autenticação via Active Directory (Kerberos AS-REQ com senha digitada), mantendo break-glass local controlado, provisionamento administrativo com `ad_upn`, proteção anti-lockout e o fluxo de papel ativo existente.

## RENAMED Requirements

- FROM: `### Requirement: Autenticação local transitória`
- TO: `### Requirement: Autenticação por Active Directory com break-glass local`

## MODIFIED Requirements

### Requirement: Autenticação por Active Directory com break-glass local

Usuários provisionados com `ad_upn` SHALL autenticar exclusivamente via Kerberos contra o Active Directory (CPF normalizado + senha do AD); credenciais locais SHALL ser inutilizáveis para esses usuários. Usuários sem `ad_upn` (break-glass, ex.: superusuário do seed) SHALL autenticar localmente apenas quando a autenticação local estiver habilitada por configuração. Independentemente do mecanismo, contas com `account_status` diferente de `active` não autenticam, e respostas ao usuário são genéricas, distinguindo apenas "credenciais inválidas" de "serviço de autenticação indisponível".

#### Scenario: Login com credenciais válidas

- **GIVEN** um usuário ativo provisionado com `ad_upn`, com papel atribuído, e a senha correta do AD
- **WHEN** submete login com CPF e senha
- **THEN** a sessão é criada e o fluxo de papel ativo é iniciado

#### Scenario: Senha AD incorreta não autentica e não faz failover

- **GIVEN** um usuário provisionado com `ad_upn` e a senha errada
- **WHEN** submete login
- **THEN** o login é negado com mensagem genérica de credenciais inválidas e nenhum segundo DC é consultado para esta tentativa

#### Scenario: Conta bloqueada não autentica

- **GIVEN** um usuário com `account_status` bloqueado e senha correta do AD
- **WHEN** submete login
- **THEN** o login é negado

#### Scenario: Usuário AD não autentica com senha local

- **GIVEN** um usuário provisionado com `ad_upn`
- **WHEN** o backend local é consultado com qualquer senha local
- **THEN** a autenticação local é recusada para esse usuário

#### Scenario: Break-glass local autentica quando habilitado

- **GIVEN** um usuário sem `ad_upn` (ex.: superusuário do seed), com senha local válida e autenticação local habilitada
- **WHEN** submete login
- **THEN** a sessão é criada normalmente

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

## ADDED Requirements

### Requirement: Provisionamento administrativo com ad_upn

O cadastro administrativo de usuários do AD SHALL registrar o `ad_upn` (único, opcional) e deixar a senha local inutilizável para esses usuários. O login SHALL ser o CPF normalizado, correspondente ao `sAMAccountName`.

#### Scenario: Usuário AD provisionado sem senha local

- **GIVEN** um administrador cadastra um usuário com `ad_upn` preenchido
- **WHEN** o usuário é consultado
- **THEN** ele não possui senha local utilizável e seu `ad_upn` é único no sistema

### Requirement: Backend Kerberos com failover de DCs

A validação Kerberos SHALL consultar os DCs configurados em ordem, avançando para o próximo DC **somente** em erro de transporte, timeout ou KDC indisponível. Códigos KDC de autenticação (`6`, `18`, `23`, `24`, `37`) são definitivos e não provocam nova tentativa. Quando todos os DCs estão inalcançáveis, o resultado é "serviço indisponível", nunca "credenciais inválidas". O resultado interno preserva o código KDC do protocolo para diagnóstico, sem depender de texto de exceção.

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

O sistema SHALL limitar tentativas de login malsucedidas por CPF e por par IP+CPF, com limiar configurável inferior à política de lockout do AD. Ao atingir o limiar dentro da janela, tentativas subsequentes são recusadas temporariamente com mensagem genérica, sem consultar o KDC e sem alterar `account_status`. Tentativa bem-sucedida reinicia os contadores.

#### Scenario: Limiar atingido bloqueia temporariamente

- **GIVEN** um CPF com tentativas malsucedidas suficientes para atingir o limiar dentro da janela
- **WHEN** uma nova tentativa de login é submetida
- **THEN** ela é recusada com mensagem genérica sem consultar o KDC

#### Scenario: Login bem-sucedido reinicia contadores

- **GIVEN** um CPF com falhas abaixo do limiar
- **WHEN** o login é bem-sucedido
- **THEN** os contadores de tentativas desse CPF são zerados
