# ADR-0003: Autenticação em dois estágios — local transitória, depois AD/Kerberos; sem e-mails

## Status

Accepted

## Contexto

O hospital autentica usuários via Active Directory. A integração Kerberos
(minikerberos) depende de infraestrutura de rede e de decisões de change
posterior (`ad-kerberos-authentication`). O bootstrap não pode ficar bloqueado
por essa dependência. Além disso, como a senha pertence ao AD, não existem
fluxos de reset/convite por e-mail no domínio do HMD.

## Decisão

- **Estágio 1 (bootstrap, transitório):** autenticação local com
  `ModelBackend` + backend custom mínimo que valida `account_status`
  (`active|blocked|removed`) e delega a checagem de senha ao ModelBackend.
  Views de login/logout próprias com templates HMD.
- **Estágio 2 (change 02 `ad-kerberos-authentication`):** substituição do
  login local comum por Kerberos (minikerberos, AS-REQ/TGT por tentativa,
  mensagens genéricas externamente, rate limit por IP+CPF).
- **Superuser local persiste como break-glass** mesmo após o change 02
  (flag de env, desabilitado por padrão) — via de acesso quando o AD estiver
  indisponível.
- **Sem e-mails transacionais — fora de escopo permanente** (ADR registra):
  senha é do AD; não há reset/convite por e-mail. Nenhuma variável de e-mail
  existe nas settings do HMD.

## Alternativas Consideradas

1. **Já usar Kerberos no bootstrap** — rejeitada: acoplaria o bootstrap à rede
   do hospital e invalidaria o gate local determinístico.
2. **Backend de e-mail para reset/convite (padrão ats-web)** — rejeitada:
   fluxo inexistente no domínio (senha gerida pelo AD).

## Consequências

- Positivas:
  - Gate local determinístico desde o primeiro commit, sem rede do hospital.
  - Superfície de autenticação local é explicitamente transitória e marcada
    para substituição no change 02.
- Negativas/Trade-offs:
  - Autenticação local transitória é superfície de ataque potencial — mitigada
    pela substituição no change 02 e pelo break-glass restrito a flag de env.
