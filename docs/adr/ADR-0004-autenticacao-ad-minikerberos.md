# ADR-0004: Autenticação AD via Kerberos AS-REQ com minikerberos fixado

## Status

Accepted

## Contexto

O hospital autentica usuários via Active Directory (realm `<REALM-AD>`, DCs
`<DC1-IP>`/`<DC2-IP>`, `sAMAccountName` = CPF). O ADR-0003 definiu a
autenticação em dois estágios: local transitória no bootstrap e substituição
por AD/Kerberos no change `ad-kerberos-authentication`. A pesquisa verificada
(`temp/research/kerberos-django.md`) recomenda, para o caso "kinit com senha
digitada" sem SSO/LDAP, o **minikerberos** (Python puro) fixado em `0.4.9`,
com os erros expostos no nível do protocolo (`KRB_ERROR.error-code`: `24`
preauth, `6` principal desconhecido, `18` conta revogada, `23` senha expirada,
`37` clock skew). LDAPS/StartTLS são inutilizáveis no ambiente e a floresta é
multi-domínio com trusts (o sufixo do UPN não é restrito a um domínio fixo).

## Decisão

- **AS-REQ por tentativa com `minikerberos==0.4.9` fixado**, num backend
  próprio (`apps.accounts.backends.KerberosBackend`) atrás de um wrapper
  injetável (`KERBEROS_CLIENT_FACTORY`) — a suíte usa fakes, zero rede.
- **Identidade**: o login digitado é o CPF (strip/lower, igual ao
  `sAMAccountName`); o **UPN completo vem do `ad_upn` cadastrado** e o realm é
  derivado do sufixo no momento da validação (floresta multi-domínio) — nunca
  configurado por env.
- **Sem SSO/SPNEGO e sem LDAP em qualquer forma** (fora de escopo); o TGT
  obtido serve apenas de prova da senha e é **descartado** (sem ccache em
  disco, sem `KRB5CCNAME`, sem reuso entre requests/workers; um cliente novo
  por tentativa).
- **Break-glass local restrito**: `LocalAccountBackend` autentica **apenas
  superusuários sem `ad_upn`** e somente com `AD_ALLOW_LOCAL_AUTH=True`
  (default `False` em base; dev/test setam `True` explicitamente). Usuários
  com `ad_upn` e usuários comuns sem `ad_upn` são recusados sempre (ADR-0003).
- **Status antes do KDC**: `account_status == "active"` é checado antes de
  qualquer AS-REQ — conta bloqueada não gera tráfego Kerberos.

## Alternativas Consideradas

1. **`python-gssapi`** — rejeitada: exige toolchain nativa krb5 no container
   slim e colapsa os códigos KDC em major/minor GSS não portáteis (o contrato
   de diagnóstico do AD se perde).
2. **Subprocesso `kinit`** — rejeitada: o parsing de stderr não é contrato
   estável; gerencia ccache/ambiente externo e estado entre processos.
3. **Pacotes Django prontos** (SPNEGO/LDAP: `django-auth-ldap`,
   `django-gss-spnego`, etc.) — rejeitados: todos assumem SSO/SPNEGO ou LDAP,
   ambos fora do escopo do HMD.
4. Escolhida: **minikerberos fixado** — Python puro (sem krb5 nativo), códigos
   no nível do protocolo, versão pinada para mitigar projeto menos auditado.

## Consequências

- **Lockout do AD** é o risco central: o failover entre DCs é **restrito a
  erro de transporte** (D5 do design) — códigos KDC de autenticação (`6`,
  `18`, `23`, `24`, `37`) são definitivos e nunca repetem a senha em outro DC;
  o slice 004 adiciona **anti-lockout local** (rate-limit por CPF e IP+CPF,
  limiar abaixo da política do AD, cache compartilhado em produção).
- **minikerberos é menos auditado**: versão fixada, superfície mínima (só
  AS-REQ), validação controlada contra os DCs reais via comando manual
  `ad_check` (fora do CI) e monitoramento de advisories.
- **Timeout por tentativa**: o socket síncrono do minikerberos 0.4.9 não
  expõe timeout — o wrapper aplica `socket.setdefaulttimeout` (que é global ao
  processo) serializando a janela da tentativa com um lock de módulo
  (decisão registrada no código, `apps/accounts/kerberos.py`).
- **NTP no host** é requisito operacional: clock skew além da janela gera o
  código `37` (documentado, não código).
- Positiva: resposta ao usuário final é genérica e distingue apenas
  "credenciais inválidas" de "serviço de autenticação indisponível" — sem
  vazar códigos KDC, existência de CPF ou status interno.
