# Slice 005: Multi-role, papel ativo em sessão e switch-role

## Objetivo

Implementar o mecanismo de papel ativo único por sessão: middleware que define o papel ativo (auto-set para 1 papel; seleção obrigatória para multi-role), view de troca de papel, context processor para templates e decorator `@role_required` protegendo views por papel ativo.

## Contexto necessário (contexto zero)

- Slices 001–004 entregues: scaffold, banco, `apps/accounts` (User/Role/seed), login/logout/home/perfil funcionando (login redireciona direto para home).
- Referência somente-leitura: `/projects/dev/ats-web/apps/accounts/middleware.py` (`ActiveRoleMiddleware`: auto-set 1 papel; redirect para `/switch-role/` quando N papéis sem papel ativo; logout/aviso quando 0 papéis), `context_processors.py` (`role_context`: papel ativo + papéis), `decorators.py` (`role_required`), `views.py` (`switch_role`), `templates/accounts/switch_role.html`.
- Design: `../design.md` D4 (papel ativo em sessão — padrão ats-web).
- Spec: `../specs/account-access/spec.md` (requisitos de multi-role/papel ativo/views protegidas — 4 cenários).
- Sessão: chave `active_role` guarda o **nome** do papel; troca invalida apenas o papel, não a sessão.

## Requisitos

- **R1** `ActiveRoleMiddleware`: autenticado com 1 papel → `session["active_role"]` auto-set; com N papéis e sem papel ativo → redirect `/switch-role/` em qualquer view protegida; com 0 papéis → logout com mensagem explicativa; anônimo → passa direto (login cuida do resto).
- **R2** View `switch_role` (GET lista papéis do usuário; POST valida que o papel escolhido pertence ao usuário, grava na sessão e redireciona à home do papel).
- **R3** Context processor expõe a todos os templates: papel ativo, lista de papéis do usuário, flag `has_multiple_roles`.
- **R4** Decorator `@role_required("doctor", ...)` nega com HTTP 403 quando o papel ativo não corresponde; redireciona anônimo ao login. **Divergência deliberada do ats-web**: lá `role_required` redireciona para `/` com mensagem; no HMD 403 é explícito (contrato da spec).
- **R5** Navbar (base.html) exibe papel ativo e, para multi-role, um menu de troca rápida de papel (dropdown ou link para switch-role).
- **R6** Ordenação de middleware documentada e aplicada: `ActiveRoleMiddleware` após `AuthenticationMiddleware`.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/middleware.py` | `test_active_role.py::test_single_role_auto_set`, `::test_multi_role_requires_selection`, `::test_zero_roles_logs_out` |
| R2 | `apps/accounts/views.py`, `templates/accounts/switch_role.html` | `test_active_role.py::test_switch_role_updates_session`, `::test_switch_role_rejects_foreign_role` |
| R3 | `apps/accounts/context_processors.py` | `test_active_role.py::test_role_context_in_template` |
| R4 | `apps/accounts/decorators.py` | `test_active_role.py::test_role_required_forbids_wrong_active_role` |
| R5 | `templates/base.html` | `test_active_role.py::test_navbar_shows_active_role` |
| R6 | `config/settings/base.py` (MIDDLEWARE) | inspeção + `uv run pytest` |

## RED

- Comando: `uv run pytest apps/accounts/tests/test_active_role.py`
- Falha esperada: 404/`NoReverseMatch`/assert de sessão — middleware/switch-role/decorator não existem.

## GREEN / verificação local

- `uv run pytest apps/accounts/tests/test_active_role.py` — exit 0
- `uv run pytest apps/accounts/tests/` — exit 0 (regressão auth/modelos)
- `uv run ruff check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/middleware.py
  - apps/accounts/context_processors.py
  - apps/accounts/decorators.py
  - apps/accounts/views.py            # + switch_role
  - apps/accounts/urls.py             # + rota switch-role
  - templates/accounts/switch_role.html
  - templates/base.html               # navbar papel ativo/troca
  - config/settings/base.py           # MIDDLEWARE + context_processors
  - apps/accounts/tests/test_active_role.py
allowed_incidental_files: []
out_of_scope:
  - IntranetGuardMiddleware (slice 006 — não criar ainda)
  - homes específicas por papel com conteúdo real (placeholders permanecem)
  - subtipos de doctor/specialties (change futuro)
```

Escale ao parent se: o middleware precisar de alterações em settings de sessão/autenticação além do registro; houver conflito com o fluxo de login do slice 004 não resolvível localmente.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz (4 cenários da spec cobertos)
- [ ] Papel ativo nunca persiste no banco (apenas sessão)
- [ ] `role_required` nega 403 (não redirect) para papel ativo errado
- [ ] Gate parcial do slice verde
