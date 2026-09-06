# Proposal: ad-kerberos-authentication

## Why

O HMD autentica hoje (bootstrap) com senha local transitória (ADR-0003). O hospital usa Active Directory (`<dominio-ad>` / realm `<REALM-AD>`, DCs `<DC1-IP>` e `<DC2-IP>`, `sAMAccountName` = CPF) e exige que contas de usuário sejam as do AD — sem LDAP/LDAPS disponível, sem SSO/SPNEGO e sem e-mails transacionais. A pesquisa verificada (`temp/research/kerberos-django.md`) recomenda validar a senha via tentativa Kerberos AS-REQ/TGT com **minikerberos** (fixado em `0.4.9`) num backend Django próprio, mantendo autorização 100% local.

## What Changes

- Campo `User.ad_upn` (UPN completo `cpf@dominio`, único, opcional): marca usuários geridos pelo AD — o ambiente é floresta multi-domínio, então o realm é derivado do sufixo do UPN; provisionamento administrativo via Django admin.
- `KerberosBackend`: usuários **com** `ad_upn` autenticam exclusivamente via Kerberos (CPF + senha do AD); checagem de `account_status` antes de qualquer AS-REQ; o TGT é descartado (prova de senha, nada é reutilizado).
- Failover entre DCs **somente** em erro de transporte/timeout (incl. código `52` com retry TCP no mesmo DC); códigos KDC de autenticação (`6`/`18`/`23`/`24`/`37`) são definitivos (evita amplificar lockout do AD).
- Taxonomia de resultado (`ok`/`code`/`reason`) extraída do protocolo, nunca de texto de exceção; "serviço indisponível" (DCs fora) é distinto de "credenciais inválidas" para o usuário — sempre com mensagem genérica.
- Proteção anti-lockout local por CPF normalizado/IP confiável via cache (limiar abaixo da política do AD; bloqueio temporário, sem tocar `account_status`; produção exige cache compartilhado).
- Break-glass local **apenas para superusuários sem `ad_upn`** e **desligado por padrão** (`AD_ALLOW_LOCAL_AUTH`, ADR-0003); ambientes dev/teste ligam explicitamente.
- Correção de UX agendada na revisão do change 01: `/switch-role/` com zero papéis encaminha ao logout com mensagem (hoje exibe página vazia).

## Capabilities

### `account-access` (delta)

- RENAMED: "Autenticação local transitória" → "Autenticação por Active Directory com break-glass local" (texto e cenários reescritos).
- MODIFIED: "Usuário multi-role com papel ativo único em sessão" (novo cenário: zero papéis na seleção → logout com mensagem).
- ADDED: provisionamento com `ad_upn`; backend Kerberos com failover de DCs; proteção anti-lockout local.

## Impact

- Arquivos: `apps/accounts/{models,backends,admin}.py` (novo `kerberos.py`, `ratelimit.py`), `config/settings/base.py`, `pyproject.toml` (minikerberos==0.4.9), `.env.example`, `docs/adr/ADR-0004*`.
- Migração: campo `ad_upn` (nullable/unique) — reversível.
- Risco: lockout de conta AD por tentativas — mitigado pelo anti-lockout local e pelo failover restrito a erros de transporte.
- Não altera: papéis, papel ativo, guard de intranet, conselho profissional (specs do change 01 permanecem válidas).

## Non-goals

- SSO/SPNEGO, keytab, LDAP/LDAPS/StartTLS (inutilizáveis no ambiente — ver pesquisa).
- Sincronização automática de usuários/grupos do AD (provisionamento é administrativo, local).
- Troca de senha AD pela aplicação (senha é do hospital; perfil continua sem fluxo de senha para usuários AD).
- Sessões/autorização novas (papéis continuam locais).
