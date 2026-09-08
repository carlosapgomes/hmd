# Design: admin-local-identity

## D1 — Admin local por design (remoção do gate de flag)

`LocalAccountBackend.authenticate` (apps/accounts/backends.py) remove a
consulta a `settings.AD_ALLOW_LOCAL_AUTH`; permanecem as regras herméticas
existentes: usuário existe + `ad_upn` vazio + `is_superuser` +
`check_password` + `user_can_authenticate` (ativo + `account_status=active`).
Custo constante para usuário inexistente permanece. A decisão e o fundamento
(credencial assistencial única no AD — dono, 2026-09-08) são registrados no
**ADR-0009**, que emenda o modelo do ADR-0003/0004: break-glass deixa de ser
estado de emergência e passa a ser a identidade permanente do admin;
preencher `ad_upn` no superuser continua sendo o caminho para torná-lo AD
(regra "sem ad_upn" é o que o mantém local — deliberado).

## D2 — Extinção do `AD_ALLOW_LOCAL_AUTH`

Remover de `config/settings/{base,dev,test}.py` e `.env.example`. Sem
mig-ração de valor: ausência da flag no ambiente é simplesmente ignorada.
Testes que hoje exercitam o gate (`override_settings(AD_ALLOW_LOCAL_AUTH=...)`)
são reescritos para o contrato novo (sem flag: local autentica; hermético para
os demais perfis).

## D3 — Anti-lockout no login do Django admin

O login do admin usa `django.contrib.admin` com a view própria do Django —
sem hook de view. Solução: **`HmdAdminSite(admin.AdminSite)`** subclass com
`login()` que envolve o comportamento padrão (`super().login`) e:

1. **Pre-check**: em POST com username, se `is_login_locked(request,
   username)` → resposta imediata (re-render do form com mensagem genérica,
   sem chamar `super().login` → sem tentativa de autenticação).
2. **Pós-processo**: POST que retorna 200 (form re-renderizado = falha) e cujo
   username resolve para **superuser sem `ad_upn`** → `register_failed_login`
   (falha determinística de senha local); POST que retorna redirect (sucesso)
   → `clear_login_failures`. Outros perfis no `/admin` (ex.: médico com
   `is_staff` via Kerberos) **não registram** falha nesta rota — preserva a
   emenda do change 02 (indisponibilidade de AD não conta; o login assistiral
   continua na view customizada, que já classifica por resultado do backend).

Wiring: `HmdAdminConfig(AdminConfig)` com `default_site` em
`config/admin.py`; `INSTALLED_APPS` troca `django.contrib.admin` por
`config.admin.HmdAdminConfig` (o `admin.site` global passa a ser a subclass —
registros existentes em `apps/*/admin.py` continuam funcionando sem mudança).

## D4 — Migrações e eventos

Nenhuma migration (nenhum campo/model muda); nenhum evento novo. `seed_admin`
permanece (superuser local com senha real e sem `ad_upn`).
