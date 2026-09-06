# Slice 001: `ad_upn` e provisionamento administrativo

## Objetivo

Marcar a origem AD da identidade: campo `User.ad_upn` (único, opcional), migration, e Django admin para User/Role — a superfície onde administradores cadastram usuários AD (com `set_unusable_password()`) e atribuem papéis. Comportamento observável: admin cadastra/edita usuários com `ad_upn`; `ad_upn` é único; usuários do seed/.break-glass continuam sem `ad_upn`.

## Contexto necessário (contexto zero)

- Change anterior entregou `apps/accounts` completo (User com `account_status`/conselho/roles, seed, auth local, papel ativo). Leia `apps/accounts/models.py` e `apps/accounts/tests/test_models.py` para convenções.
- Design deste change: `../design.md` D3 (ad_upn marca origem; username = CPF normalizado; sem sync automático).
- Spec: `../specs/account-access/spec.md` — Requirement ADDED "Provisionamento administrativo com ad_upn".
- Login = CPF: `username` já é o campo de login; normalização strip/lower é comportamento do backend (slice 003), não deste slice.
- Não há `apps/accounts/admin.py` hoje — o Django admin nativo não exibe o User custom sem registro; este slice o cria.

## Requisitos

- **R1** `User.ad_upn`: `CharField(max_length=150, unique=True, null=True, blank=True)` com validação de formato **UPN completo** (`RegexValidator` tipo `user@dominio` — sufixo livre: floresta multi-domínio, ver design D3); migration gerada e aplicável em banco limpo.
- **R2** `apps/accounts/admin.py` registra `User` (list_display com username/ad_upn/account_status/is_staff; fieldsets/searchable por username/ad_upn; filtro por account_status; M2M roles editável) e `Role`.
- **R3** No admin, salvar usuário com `ad_upn` preenchido deixa a senha local inutilizável (`set_unusable_password()` via lógica do formulário/admin; usuários existentes locais preservam senha).
- **R4** Testes: `ad_upn` único (violação gera IntegrityError/ValidationError); nullable em massa (seed/break-glass); UPN inválido (sem `@dominio`) rejeitado pela validação de formato (sufixos de outros domínios da floresta são aceitos); admin changelist/detail renderizam com o campo; salvar com `ad_upn` resulta em `has_usable_password() is False`.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/models.py`, `apps/accounts/migrations/000X_ad_upn.py` | `test_ad_upn.py::test_unique`, `::test_nullable` |
| R2 | `apps/accounts/admin.py` | `test_ad_upn.py::test_admin_changelist_renders`, `::test_admin_form_includes_fields` |
| R3 | `apps/accounts/admin.py` (form/save) | `test_ad_upn.py::test_ad_user_has_unusable_password` |
| R4 | `apps/accounts/tests/test_ad_upn.py` | `uv run pytest apps/accounts/tests/test_ad_upn.py` |

## RED

- Comando: `uv run pytest apps/accounts/tests/test_ad_upn.py` (com compose de teste no ar)
- Falha esperada: `AttributeError: 'User' object has no attribute 'ad_upn'` / import de campo inexistente — prova que o provisionamento AD não existe.

## GREEN / verificação local

- `uv run pytest apps/accounts/tests/test_ad_upn.py` — exit 0
- `uv run pytest apps/accounts/tests/` — exit 0 (regressão do app)
- `uv run ruff check . && uv run mypy .` — exit 0
- `uv run python manage.py migrate --settings=config.settings.dev` (compose dev) — migration aplica

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/models.py
  - apps/accounts/migrations/000X_ad_upn.py
  - apps/accounts/admin.py
  - apps/accounts/tests/test_ad_upn.py
allowed_incidental_files:
  - apps/accounts/tests/conftest.py (fixtures mínimas, se necessário)
out_of_scope:
  - backends/kerberos/ratelimit (slices 002–004)
  - normalização de username no login (slice 003)
  - admin_ui customizado além do Django admin nativo (não existe no roadmap)
```

Escale ao parent se: o admin exigir fieldsets muito além do listado; precisar de dependência nova; a migration exigir data migration.

## Critérios de aceitação

- [ ] R1–R4 comprovados pelos comandos da matriz
- [ ] Seed/superusuário existente não ganha `ad_upn` (break-glass preservado)
- [ ] Migration reversível e aplicável em banco limpo
- [ ] Gate parcial do slice verde
