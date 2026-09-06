# Slice 003: Modelos de conta e seed administrativo

## Objetivo

Criar o app `apps/accounts` com o modelo de usuário custom (`AUTH_USER_MODEL`) e os cinco papéis fixos, incluindo status de conta, registro de conselho profissional e o comando `seed_admin` idempotente. Comportamento observável: seed cria papéis + superusuário multi-role sem duplicar em execuções repetidas, e validações do modelo são aplicadas.

## Contexto necessário (contexto zero)

- Slices 001–002 entregues: scaffold + banco/compose + gate verde.
- Referência somente-leitura: `/projects/dev/ats-web/apps/accounts/models.py` (`User`, `Role`, `account_status`, `professional_council` + `clean()` par-ou-nada, `display_name`/`professional_registration_display`), `/projects/dev/ats-web/apps/accounts/management/commands/seed_admin.py` (`ALL_ROLES` com 5 papéis; **nota**: o seed do ats-web usa argumentos CLI com defaults — o HMD usa env vars, divergência deliberada para uso em compose/CI).
- Design: `../design.md` D8 (User estendido uma única vez — sem `ad_upn`/`specialties` aqui).
- Spec: `../specs/account-access/spec.md` (papéis fixos, seed idempotente, conselho consistente).
- HMD usa os mesmos 5 papéis do ats-web: `nir`, `doctor`, `scheduler`, `manager`, `admin`.

## Requisitos

- **R1** `Role` com campo nome único; `User(AbstractUser)` com `roles` M2M para `Role`, `account_status` (`active|blocked|removed`, default `active`), `professional_council` (`CRM|COREN`, opcional) e `professional_council_number` (opcional).
- **R2** `User.clean()` rejeita conselho preenchido sem número e número sem conselho, com `ValidationError` **associada ao campo** (`{"professional_council": ...}` ou `{"professional_council_number": ...}`) — divergência deliberada do ats-web, que lança erro não associado a campo (melhor UX de formulário).
- **R3** `display_name` (property) = `get_full_name() or username`; `professional_registration_display` = `"CRM 12345"` quando conselho preenchido, `""` caso contrário.
- **R4** `manage.py seed_admin` cria (idempotentemente) os 5 papéis e um superusuário multi-role com os 5 papéis, credenciais via env (`DJANGO_SUPERUSER_USERNAME/PASSWORD`), senha hasheada; não duplica nada em segunda execução.
- **R5** `AUTH_USER_MODEL = "accounts.User"` configurado; migrações do app geradas e aplicáveis no banco de teste.
- **R6** Seed usa interface de administração padrão: `superuser.is_staff = is_superuser = True` (acesso ao Django admin para bootstrap; admin_ui dedicado chega em change posterior).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/models.py`, `apps/accounts/migrations/0001_initial.py` | `uv run pytest apps/accounts/tests/test_models.py` |
| R2 | `apps/accounts/models.py` | `test_models.py::test_council_requires_pair` |
| R3 | `apps/accounts/models.py` | `test_models.py::test_display_name_fallback` |
| R4 | `apps/accounts/management/commands/seed_admin.py` | `test_seed_admin.py::test_seed_idempotent` (2 execuções) |
| R5 | `config/settings/base.py`, migrations | `uv run pytest` (banco de teste cria/aplica) |
| R6 | `apps/accounts/management/commands/seed_admin.py` | `test_seed_admin.py::test_superuser_flags` |

## RED

- Comando: `uv run pytest apps/accounts/tests/`
- Falha esperada: collection error — app `apps.accounts` não existe.

## GREEN / verificação local

- `uv run pytest apps/accounts/tests/` — exit 0
- `uv run pytest` — exit 0 (regressão: smoke + db + accounts)
- `uv run python manage.py migrate --settings=config.settings.dev` — aplica a migration nova no compose dev
- `uv run ruff check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/__init__.py
  - apps/accounts/apps.py
  - apps/accounts/models.py
  - apps/accounts/migrations/0001_initial.py
  - apps/accounts/management/{__init__,commands/seed_admin}.py (estrutura)
  - apps/accounts/tests/{__init__,test_models,test_seed_admin}.py
  - config/settings/base.py        # INSTALLED_APPS + AUTH_USER_MODEL
allowed_incidental_files:
  - conftest.py (fixtures mínimas de usuário/papel se necessário)
out_of_scope:
  - views/login/logout/templates (slice 004)
  - middleware/decorators (slices 005/006)
  - ad_upn e specialties (changes 02/03 — não criar campos agora)
```

Escale ao parent se: precisar alterar o modelo `User` além do especificado; precisar de dependência nova; a migration exigir dados além do schema.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz
- [ ] Seed roda duas vezes sem duplicar (R4 verificado)
- [ ] Migration aplicável em banco limpo
- [ ] Gate parcial do slice verde
