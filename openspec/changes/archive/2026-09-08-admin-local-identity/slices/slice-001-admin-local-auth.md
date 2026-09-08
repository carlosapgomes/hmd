# Slice 001: Admin local por design + anti-lockout no Django admin

## Objetivo

O superusuário sem `ad_upn` autentica localmente em qualquer ambiente (sem
flag), o login do Django admin fica coberto pelo anti-lockout (pre-check +
contagem apenas para o perfil local) e a decisão é registrada no ADR-0009.

## Contexto necessário

- `apps/accounts/backends.py::LocalAccountBackend` (linhas ~120–160): gate
  `AD_ALLOW_LOCAL_AUTH` a remover; regras herméticas a preservar (sem
  `ad_upn`, `is_superuser`, custo constante para usuário inexistente,
  `user_can_authenticate` com `account_status`).
- `apps/accounts/ratelimit.py::{register_failed_login, is_login_locked,
  clear_login_failures}` — API existente (chaves IP+CPF; a view de login em
  `apps/accounts/views.py:68–86` é o padrão de uso).
- `config/settings/{base,dev,test}.py` — `AD_ALLOW_LOCAL_AUTH` (base define +
  comentário; dev/test setam True) e `AUTHENTICATION_BACKENDS`; `.env.example`
  linha da flag.
- `config/settings/base.py::INSTALLED_APPS` — `"django.contrib.admin"` (trocar
  por `AdminConfig` custom); padrão Django: subclass de `AdminConfig` com
  `default_site` apontando para `HmdAdminSite(admin.AdminSite)` em
  `config/admin.py` (novo) — `admin.site` global passa a ser a subclass e os
  `admin.site.register` existentes continuam válidos.
- `apps/accounts/management/commands/seed_admin.py` — superuser local (sem
  mudança; é a identidade do admin).
- ADRs existentes em `docs/adr/` (0003 autenticação dois estágios; 0004 AD
  minikerberos) — o ADR-0009 os emenda.
- Tests existentes que assumem o gate: `apps/accounts/tests/test_kerberos_backend.py`
  (8× `override_settings`; `test_breakglass_superuser_only_and_flag` ~241–305
  assere "flag False → recusa" — quebra com a remoção e vira o contrato novo) e
  docstrings em `test_kerberos_backend.py:13` / `test_auth_flow.py:5,43`
  (mencionam a flag). `README.md` (raiz, ~49–56) também documenta a flag.
- `config/admin.py` é importado durante a população do app registry (antes de
  `apps.accounts`): imports de models DEVEM ser lazy (dentro do método ou
  `apps.get_model`) — mesmo padrão dos inner-imports de
  `django/contrib/admin/sites.py`.

## Requisitos verificáveis

- **R1** `LocalAccountBackend.authenticate` sem consulta à flag: superuser sem
  `ad_upn` + senha correta + ativo autentica em **settings sem a flag definida**
  (simula prod). Hermético preservado: com `ad_upn` → None; não-superuser sem
  `ad_upn` → None; senha errada → None; custo constante usuário inexistente.
- **R2** `AD_ALLOW_LOCAL_AUTH` extinto: nenhuma ocorrência em
  `config/`, `.env.example` e `apps/` (apenas menção histórica no ADR-0009,
  que cita a extinção).
- **R3** `config/admin.py::HmdAdminSite` com `login()` que: (a) em POST com
  username bloqueado (`is_login_locked`) responde cedo (200 com form
  re-renderizado + mensagem genérica) **sem** chamar `super().login` (sem
  tentativa de autenticação); (b) POST que falha (resposta 200 do fluxo padrão)
  cujo username resolve para superuser sem `ad_upn` **e com `password`
  não-vazio** (form inválido sem senha não é falha de credencial) →
  `register_failed_login` (usar a MESMA normalização de username da view de
  login — strip/lower — para não desviar dos contadores); (c) POST com sucesso
  (redirect) → `clear_login_failures`; (d) falhas de outros perfis (com
  `ad_upn`) nessa rota **não** registram.
- **R4** `HmdAdminConfig(AdminConfig)` com `default_site`; `INSTALLED_APPS`
  usa o config custom; admin do Django segue funcional (página de login e
  índice renderizam; `admin.site.register` existentes intactos).
- **R5** ADR-0009 (`docs/adr/ADR-0009-admin-identidade-local-por-design.md`):
  decisão (admin local permanente sem flag; emenda ao break-glass do
  ADR-0003/0004), fundamento (credencial assistencial única no AD — dono,
  2026-09-08), alternativas (segunda identidade no AD; flag default-on;
  break-glass mantido), consequências (superfície de força-bruta no `/admin`
  mitigada pelo anti-lockout estendido; preencher `ad_upn` no superuser o
  torna AD; **o desligamento rápido pós-extinção da flag é fora de banda:
  `account_status=blocked` no usuário via shell/banco**).
- **R6** Testes: backend (R1: autentica sem flag; hermético ×3; custo
  constante); admin login (R3: bloqueio cedo sem tocar autenticação — usar
  backend fake ou senha errada + assert de não-chamada via monkeypatch do
  `super().login`/backend; contagem por falha local; limpeza no sucesso;
  não-contagem para perfil AD); integração mínima (R4: login do admin
  renderiza; superuser local loga no `/admin` end-to-end com a flag ausente).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/backends.py` | `test_local_admin_authenticates_without_flag`, `test_local_backend_hermetic_*` |
| R2 | `config/settings/{base,dev,test}.py`, `.env.example`, `README.md` | `rg -n "AD_ALLOW_LOCAL_AUTH" config .env.example README.md apps` → vazio |
| R3 | `config/admin.py` | `test_admin_login_locked_refuses_early`, `test_admin_login_counts_local_failures`, `test_admin_login_success_clears`, `test_admin_login_ad_profile_failure_not_counted` |
| R4 | `config/settings/base.py`, `config/admin.py` | `test_admin_site_is_hmd_subclass`, `test_superuser_local_logs_into_admin` |
| R5 | `docs/adr/ADR-0009-*.md` | inspeção |
| R6 | `apps/accounts/tests/test_local_admin.py` (+ajustes nos que usavam a flag) | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/backends.py            # remoção do gate (mínima)
  - apps/accounts/tests/test_local_admin.py
  - apps/accounts/tests/test_kerberos_backend.py  # reescreve testes do gate p/ contrato novo
  - apps/accounts/tests/test_auth_flow.py         # docstrings da flag
  - config/admin.py                      # HmdAdminSite + HmdAdminConfig (novo)
  - config/settings/base.py              # INSTALLED_APPS + remoção da flag
  - config/settings/dev.py               # remoção da flag
  - config/settings/test.py              # remoção da flag
  - .env.example
  - README.md                            # seção da flag (~49-56)
  - docs/adr/ADR-0009-admin-identidade-local-por-design.md

out_of_scope:
  - KerberosBackend/failover/rate-limit core (apps/accounts/ratelimit.py)
  - seed_admin; self-service de senha; 2FA
  - UI de login; outros papéis
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_local_admin.py`
- Falha esperada: `ModuleNotFoundError: No module named 'config.admin'`
  (import do `HmdAdminSite` no teste; o backend sem flag falha nos asserts de
  autenticação com settings sem `AD_ALLOW_LOCAL_AUTH`).

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/` — exit 0 (regressão
  do app inteiro: backends/views/ratelimit/specialties).
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .`
- `uv run python manage.py check --settings=config.settings.test`

## Critérios de aceitação

- [ ] R1–R6 comprovados; `rg AD_ALLOW_LOCAL_AUTH` vazio em código/env/README
      (só ADR-0009)
- [ ] Superuser local loga no `/admin` end-to-end sem nenhuma flag definida
- [ ] Bloqueio do anti-lockout recusa cedo no `/admin` sem tocar autenticação;
      POST sem senha não conta como falha
- [ ] Purpose da spec `account-access` atualizado pelo parent no archive
      ("break-glass local controlado" → admin local por design)
- [ ] Gate parcial do slice verde
