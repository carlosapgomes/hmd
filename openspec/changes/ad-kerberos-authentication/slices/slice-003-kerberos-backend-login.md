# Slice 003: KerberosBackend, login e ADR-0004

## Objetivo

Ligar o cliente Kerberos à autenticação: `KerberosBackend` (usuários com `ad_upn` autenticam via AD), `LocalAccountBackend` recusando usuários AD, ordem de backends, mensagens de login distinguindo "credenciais inválidas" de "serviço indisponível", e o comando de diagnóstico `ad_check`. Fecha com o ADR-0004 registrando a decisão.

## Contexto necessário (contexto zero)

- Slices 001–002 deste change entregues: `ad_upn` + admin (001), `kerberos.py` com `KerberosAuthResult`/failover/factory injetável (002).
- Atual: `apps/accounts/backends.py` tem `LocalAccountBackend` (ModelBackend + `account_status == "active"`); `apps/accounts/views.py` tem `login_view` com mensagem genérica única; `AUTHENTICATION_BACKENDS` em `config/settings/base.py` lista só o local.
- Design: `../design.md` D4 (dois backends, regra hermética por ad_upn, `AD_ALLOW_LOCAL_AUTH`), D5 (mensagens), D9 (`ad_check`).
- Spec: Requirement RENAMED/MODIFIED "Autenticação por Active Directory com break-glass local" (5 cenários).
- Sem rede no CI: `KerberosBackend` usa `KERBEROS_CLIENT_FACTORY`; testes injetam fakes (mesma técnica do slice 002).

## Requisitos

- **R1** `KerberosBackend.authenticate`: normaliza username (strip/lower); usuário inexistente ou **sem** `ad_upn` → `None` (sem consultar KDC); com `ad_upn` → `validate_with_failover`; sucesso → retorna usuário se `account_status == "active"`; falha → `None` (log interno com code/reason, sem detalhe externo).
- **R2** `LocalAccountBackend` modificado: recusa usuários **com** `ad_upn` (sem checar senha local); sem `ad_upn` mantém comportamento atual **e** exige `AD_ALLOW_LOCAL_AUTH=True` (default `True`).
- **R3** `AUTHENTICATION_BACKENDS = [KerberosBackend, LocalAccountBackend]` (ordem: AD primeiro; local só para break-glass).
- **R4** `login_view`: quando o resultado interno é `all_kdcs_unreachable`/`kdc_timeout`, a mensagem genérica é de **serviço indisponível** ("Não foi possível falar com o serviço de autenticação. Tente novamente.") — distinta de credenciais inválidas; nenhuma mensagem revela código KDC, existência de CPF ou status interno.
- **R5** Management command `ad_check`: `uv run python manage.py ad_check --cpf <cpf>` (senha via prompt/env, nunca argv) executa a validação contra **cada** DC configurado isoladamente e imprime por DC: ok/código/razão/latência; documentado no README/`.env.example` como ferramenta de aceitação fora do CI.
- **R6** ADR-0004 (`docs/adr/ADR-0004-autenticacao-ad-minikerberos.md`): contexto, decisão (AS-REQ/minikerberos fixado, sem SSO/LDAP, break-glass por ad_upn), alternativas (gssapi, kinit subprocesso, pacotes pratos) e consequências (lockout AD → D5/D7 do design; TGT descartado).
- **R7** Testes (fakes): login AD válido cria sessão; senha errada nega (fake devolve 24, sem failover); usuário AD + senha local correta no banco → negado (R2); break-glass sem ad_upn autentica local com `AD_ALLOW_LOCAL_AUTH=True` e falha com `False`; conta `blocked` com senha AD válida → negada; `all_kdcs_unreachable` → mensagem de serviço indisponível na resposta.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/backends.py` | `test_kerberos_backend.py::test_ad_user_login_ok`, `::test_wrong_password_denied_no_failover` |
| R2 | `apps/accounts/backends.py` | `::test_ad_user_cannot_use_local_password`, `::test_breakglass_requires_flag` |
| R3 | `config/settings/base.py` | `rg -n "KerberosBackend" config/settings/base.py` |
| R4 | `apps/accounts/views.py` | `::test_all_dcs_down_shows_service_unavailable` |
| R5 | `apps/accounts/management/commands/ad_check.py` | `--help` imprime uso; verificação real é manual |
| R6 | `docs/adr/ADR-0004*.md` | inspeção: seções completas |
| R7 | `apps/accounts/tests/test_kerberos_backend.py` | `uv run pytest apps/accounts/tests/test_kerberos_backend.py` |

## RED

- Comando: `uv run pytest apps/accounts/tests/test_kerberos_backend.py`
- Falha esperada: `ImportError` (backend não existe) / asserts de sessão falham — autenticação AD não está ligada.

## GREEN / verificação local

- `uv run pytest apps/accounts/tests/test_kerberos_backend.py` — exit 0
- `uv run pytest apps/accounts/tests/` — exit 0 (regressão do app)
- `uv run ruff check . && uv run mypy .` — exit 0
- `uv run python manage.py ad_check --help` — imprime uso (sem rede)

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/backends.py
  - apps/accounts/views.py
  - apps/accounts/management/commands/ad_check.py
  - apps/accounts/tests/test_kerberos_backend.py
  - config/settings/base.py
  - docs/adr/ADR-0004-autenticacao-ad-minikerberos.md
  - README.md (seção ad_check)
allowed_incidental_files:
  - apps/accounts/tests/conftest.py (fixture de fake factory, se necessário)
out_of_scope:
  - rate-limit/anti-lockout (slice 004)
  - switch-role zero papéis (slice 005)
  - templates (mensagens usam o framework de messages existente)
  - execução real do ad_check contra DCs (manual, fora do CI)
```

Escale ao parent se: a distinção de mensagens exigir mudar o fluxo de templates; o login view precisar de reescrita além da mensagem; houver conflito com a spec RENAMED.

## Critérios de aceitação

- [ ] R1–R7 comprovados pelos comandos da matriz (5 cenários da spec cobertos)
- [ ] Nenhum teste toca a rede; `ad_check` é o único caminho real (manual)
- [ ] Senha nunca aparece em argv/logs
- [ ] Gate parcial do slice verde
