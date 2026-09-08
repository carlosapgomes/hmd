# account-access Specification (delta)

## REMOVED Requirements

### Requirement: Autenticação por Active Directory com break-glass local

**Reason**: O break-glass de emergência (habilitado por `AD_ALLOW_LOCAL_AUTH`, default desligado em produção) não reflete a realidade operacional: o admin do hospital usa a credencial do AD exclusivamente para atividades assistenciais e não pode ter segunda identidade no AD — o admin do sistema é uma **identidade local permanente** (ADR-0009).
**Migration**: Substituída por "Autenticação por Active Directory com admin local por design" (mesmo bloco abaixo, em ADDED), que preserva todos os cenários válidos e substitui o cenário de break-glass por autenticação local sem flag.

## ADDED Requirements

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

## MODIFIED Requirements

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
