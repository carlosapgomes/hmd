# Design: ad-kerberos-authentication

## Contexto

Pesquisa verificada em `temp/research/kerberos-django.md` (set/2026): para "kinit com senha digitada" sem SSO/LDAP, a recomendação é **minikerberos** fixado (`0.4.9`) num backend próprio — Python puro (sem toolchain krb5 no Docker slim), erros expostos no nível do protocolo (`KRB_ERROR.error-code`: `24` preauth, `6` principal desconhecido, `18` conta revogada, `23` senha expirada, `37` clock skew). Ambiente: realm `<REALM-AD>`, DCs `<DC1-IP>`/`<DC2-IP>` (porta 88 funcional, validada na PoC), `sAMAccountName` = CPF, LDAPS/StartTLS inutilizáveis. ADR-0003 (auth em 2 estágios) definiu este estágio 2.

### D1 — minikerberos fixado, backend próprio

`minikerberos==0.4.9` no `pyproject.toml` (nenhuma outra dependência nova). Alternativas rejeitadas: `python-gssapi` (toolchain nativa no container; códigos KDC viram major/minor GSS não portáteis), subprocesso `kinit` ( parsing de stderr não é contrato), pacotes Django prontos (todos SPNEGO/LDAP, fora do escopo). Ressalva registrada: projeto menor/menos auditado → versão fixada + teste controlado contra os DCs reais (`ad_check`, D9) + monitorar advisories.

### D2 — Uma tentativa = um cliente; TGT é descartado

Cada validação constrói credenciais/target e um cliente minikerberos **novo** (thread-safety); em sucesso, o TGT obtido serve apenas de prova de senha e é abandonado (sem ccache em disco, sem `KRB5CCNAME`, sem reuso entre requests/workers). Nunca logar senha, AS-REQ bruto ou chaves derivadas.

### D3 — `ad_upn` (UPN completo) marca a origem da identidade

`User.ad_upn` (`CharField` único, opcional/blank) = **UPN completo** `cpf@dominio` (validação de formato `user@domínio`); `username` = CPF normalizado (strip/lower), igual ao `sAMAccountName`. O ambiente é uma **floresta multi-domínio com trusts** (`temp/integracao-django-ad.md`): o sufixo do UPN não é restrito a `<dominio-ad>` — o realm de autenticação é **derivado do sufixo** no momento da validação. Os DCs configurados (`AD_DCS`) são os KDCs conhecidos do domínio raiz; KDCs de outros domínios da floresta, se necessários, são extensão futura (mapa realm→KDCs) fora deste change. Provisionamento é administrativo (Django admin, `apps/accounts/admin.py`): usuários AD são criados com `set_unusable_password()`. Sem sincronização automática de contas/grupos.

### D4 — Dois backends, regra hermética por `ad_upn`; status antes do KDC

`AUTHENTICATION_BACKENDS = ["apps.accounts.backends.KerberosBackend", "apps.accounts.backends.LocalAccountBackend"]`:

- `KerberosBackend`: carrega o usuário pelo CPF normalizado; usuário inexistente ou **sem** `ad_upn` → `None` (sem consultar KDC); **checa `account_status == "active"` antes de qualquer AS-REQ** (conta bloqueada não gera tráfego Kerberos nem conta para lockout AD); com `ad_upn` → `validate_with_failover` (D6); sucesso → retorna usuário.
- `LocalAccountBackend` (modificado): **apenas superusuários sem `ad_upn`** autenticam localmente (ADR-0003: break-glass é do superusuário) e somente com `AD_ALLOW_LOCAL_AUTH=True` (**default `False`** em `base`; settings de `dev`/`test` setam `True` explicitamente). Usuário comum sem `ad_upn` é recusado; usuário com `ad_upn` é recusado em qualquer caso.

**Contrato de resultado p/ a view**: `authenticate()` do Django devolve usuário ou `None`; para a view distinguir "serviço indisponível" de "credenciais inválidas" (D5), o `KerberosBackend` marca `request.kerberos_unavailable = True` (request-scoped) quando o resultado é `all_kdcs_unreachable`/`kdc_timeout`, e a `login_view` lê esse atributo para escolher a mensagem genérica. Ambos os backends aplicam `user_can_authenticate` com `account_status == "active"`.

### D5 — Failover restrito a erro de transporte

Falha em um DC só prova a senha no **segundo** DC quando o erro for de transporte (timeout/recusa/KDC indisponível). Códigos KDC de autenticação (`6`, `18`, `23`, `24`, `37`) são definitivos — repetir a senha em outro DC pode contar dobrado para lockout do AD (documentação Microsoft citada na pesquisa). Casos especiais: `25` (preauth required) é passo interno do fluxo `get_TGT`, nunca resultado final; `52` (resposta grande p/ UDP) é retratado via **TCP no mesmo DC** antes de qualquer classificação — só persistindo como erro de transporte conta para failover. Timeout por tentativa `AD_KDC_TIMEOUT` (default 3s). Resultado `all_kdcs_unreachable` → resposta ao usuário "serviço indisponível" (genérica), distinta de credenciais inválidas; interno loga código, DC consultado e latência.

### D6 — Taxonomia de resultado testável sem rede

`apps/accounts/kerberos.py` define `KerberosAuthResult(ok, code, reason)` e um cliente minikerberos isolado atrás de uma factory injetável (`KERBEROS_CLIENT_FACTORY` em settings, default aponta o wrapper real). O wrapper recebe **(upn, senha, dc, timeout)** e deriva o realm do sufixo do UPN (maiúsculas); a extração do código lê atributos do objeto de exceção (`errorcode`/`error_code`/`code`/`krb_error.native["error-code"]`), nunca texto da mensagem — o contrato é o protocolo. O `AD_KDC_TIMEOUT` deve ser propagado à API de socket/timeout do minikerberos na versão fixada (confirmar o parâmetro exato na 0.4.9 durante a implementação; se indisponível, envolver a tentativa com timeout explícito e registrar a decisão no código). Toda a suíte automatizada usa fakes da factory (zero rede); a validação contra DCs reais é o comando manual `ad_check` (D9).

### D7 — Anti-lockout local via cache Django

`apps/accounts/ratelimit.py`: contadores por `cpf` **normalizado** (mesma normalização do backend: strip/lower — variações de formatação não criam contadores novos) e por `ip+cpf` — o IP vem do mesmo helper confiável do guard de intranet (`_get_client_ip`/`TRUSTED_PROXY_HEADER`; nunca header arbitrário) — no cache do Django. `LOGIN_ATTEMPTS_LIMIT` (default 5), `LOGIN_ATTEMPTS_WINDOW_SECONDS` (default 900), `LOGIN_LOCKOUT_SECONDS` (default 900). A checagem roda **antes** de consultar qualquer backend/KDC; bloqueado = recusa com mensagem genérica, sem tocar `account_status` (temporário). Sucesso zera contadores. Limiar default intencionalmente abaixo da política típica de lockout do AD.

**Cache em produção**: `LocMemCache` é por-processo — com múltiplos workers, o limiar efetivo seria multiplicado. `config/settings/prod.py` falha fechado (`ImproperlyConfigured`) se `CACHES` ainda for o default LocMem em produção, exigindo cache compartilhado (Redis/Memcached) no deploy; dev/teste continuam em LocMem (limiar por processo é suficiente lá).

### D8 — Correção UX: switch-role com zero papéis

`switch_role_view` (GET e POST) valida a lista de papéis do usuário: vazia → encerra a sessão e redireciona ao login com `NO_ROLES_MESSAGE` (mesma mensagem do middleware). O middleware já cobre paths não isentos; o fix fecha o buraco do path isento. Registrado como nota de arquivamento do change 01 e vira cenário da spec (MODIFIED).

### D9 — Verificação real opcional: `manage.py ad_check`

Comando de diagnóstico fora do CI (requer rede hospitalar): `uv run python manage.py ad_check --cpf <cpf>` executa uma validação contra cada DC configurado e reporta ok/código/latência por DC. A senha é lida **exclusivamente via `getpass`** (stdin/prompt), nunca argv nem variável de ambiente (a pesquisa recomenda não colocar senha em env). Serve para a matriz de aceitação da pesquisa antes de produção (senha errada=24, CPF inexistente=6, DC .19 fora→.21, etc.) sem acoplar CI à infraestrutura do hospital.

### D10 — Variáveis de ambiente novas

`AD_DCS` (default `<DC1-IP>,<DC2-IP>`), `AD_KDC_TIMEOUT` (default `3`), `AD_ALLOW_LOCAL_AUTH` (default `False` em `base`; `True` via settings de dev/teste), `LOGIN_ATTEMPTS_LIMIT/WINDOW_SECONDS/LOCKOUT_SECONDS` (defaults de D7). Todas documentadas no `.env.example`. Nota operacional (documentada, não código): NTP no host — clock skew gera `37`. O realm não é configurável por env: é derivado do sufixo do `ad_upn` (D3).

## Riscos e mitigações

- **Lockout AD**: failover restrito (D5) + anti-lockout local (D7) + limiar abaixo do AD.
- **minikerberos menos auditado**: versão fixada; TGT descartado; superfície mínima (só AS-REQ); `ad_check` para aceitação contra DCs reais.
- **Falsos "senha inválida" por incidente de rede**: distinção serviço-indisponível vs credenciais-inválidas na UI (D5/D6).

## Decisões explicitly fora de escopo

SSO/SPNEGO, LDAP em qualquer forma, sync de usuários/grupos, troca de senha AD, cache de autenticação de sucesso além da sessão.
