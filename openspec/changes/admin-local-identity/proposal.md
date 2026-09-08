# Proposal: admin-local-identity

## Problema

O design atual (change 02, ADR-0003/0004) trata a autenticação local como
**break-glass de emergência** habilitado por `AD_ALLOW_LOCAL_AUTH` (default
`false` em produção). Restrição operacional do dono (2026-09-08): sua
credencial do AD é usada para **atividades assistenciais** e o hospital não
permite uma segunda identidade no AD — portanto o admin do sistema é, **por
natureza, uma identidade local** (superuser sem `ad_upn`), não um cenário de
emergência. Em produção hoje ele não consegue logar (nem no `/admin`), e o
kill-switch que o travaria de propósito não tem uso são.

Agravante de segurança: o login do Django admin (`/admin/login/`) não passa
pela view customizada — o anti-lockout do change 02 (IP+CPF) não o cobre.
Assim que o admin local passa a autenticar sempre, o `/admin` vira a
superfície de força-bruta da senha local.

## Objetivo

1. Admin local **por design**: `LocalAccountBackend` autentica superusuário
   sem `ad_upn` em **qualquer ambiente**, sem flag (regras herméticas
   permanecem: sem `ad_upn` + superusuário + ativo).
2. `AD_ALLOW_LOCAL_AUTH` **extinto** (settings base/dev/test, `.env.example`,
   docs) — quem o setasse travaria a única identidade não-AD.
3. Anti-lockout estendido ao login do Django admin: pre-check de bloqueio +
   registro de falhas **apenas para o perfil de autenticação local**
   (superuser sem `ad_upn` — falha determinística de senha); tentativas de
   outros perfis no `/admin` não contam (falha AD por indisponibilidade
   continua fora dos contadores, preservando a emenda do change 02).
4. ADR-0009 registrando a decisão e o fundamento (credencial assistencial
   única).

## Escopo

**Inclui**: `LocalAccountBackend` (remoção do gate), remoção da flag em
settings/env/docs, `HmdAdminSite` (subclass com login protegido, ligado por
`AdminConfig.default_site`), ADR-0009, delta spec `account-access` (2
requirements MODIFIED), ajuste dos testes existentes que assumem o gate.

**Não inclui**: mudanças no `KerberosBackend`/failover/rate-limit core;
self-service de senha; 2FA; mudanças em outros papéis (todos os demais
continuam exclusivamente Kerberos); UI nova.

## Sucesso

- Em settings de produção (sem nenhuma flag), o superuser sem `ad_upn`
  autentica no `/admin` e no login comum com senha local.
- Usuário com `ad_upn` e usuário comum sem `ad_upn` continuam recusados no
  backend local (hermético, sem flag).
- Brute-force no `/admin` é limitado pelos mesmos contadores IP+CPF (com
  pre-check recusando cedo); falhas de AD por indisponibilidade no `/admin`
  não alimentam contadores.

## Capabilities

- `account-access` (MODIFIED — 2 requirements: autenticação AD/admin local e
  anti-lockout).
