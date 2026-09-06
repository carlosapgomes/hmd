# Slice 005: Switch-role com zero papéis encerra sessão

## Objetivo

Fechar a nota de arquivamento do change 01: a tela de seleção de papel (`/switch-role/`, path isento do middleware) processada por usuário **sem nenhum papel** encerra a sessão com mensagem explicativa, em vez de exibir lista vazia. Comportamento observável: GET e POST com 0 papéis → logout + redirect ao login com `NO_ROLES_MESSAGE`.

## Contexto necessário (contexto zero)

- Por que existe: `ActiveRoleMiddleware` desloga usuários com 0 papéis em paths não isentos, mas `/switch-role/` é isento (precisa ser acessível para a troca) — a view não validava lista vazia. Nota registrada no `tasks.md` do change `bootstrap-django-hmd-core` (7.2) e cenário novo na spec deste change.
- Leia `apps/accounts/views.py::switch_role_view` e `apps/accounts/middleware.py` (constante `NO_ROLES_MESSAGE`, `EXEMPT_PATHS`).
- Spec: `../specs/account-access/spec.md` — cenário "Seleção de papel com zero papéis encaminha ao logout" (dentro do requirement MODIFIED).
- Logout do sistema é POST (`logout_view`); o encerramento programático usa `django.contrib.auth.logout()` + flush de sessão + redirect ao login com mensagem (mesmo padrão do middleware).

## Requisitos

- **R1** `switch_role_view` (GET): usuário autenticado com 0 papéis → `auth.logout(request)`, mensagem `NO_ROLES_MESSAGE` via messages framework, redirect ao login (302).
- **R2** `switch_role_view` (POST): mesma validação antes de qualquer processamento de payload (0 papéis → mesmo comportamento de R1).
- **R3** Usuários com ≥1 papel: comportamento inalterado (listagem/POST/troca continuam como no change 01 — regressão coberta pelos testes existentes de `test_active_role.py`).
- **R4** Testes: GET com 0 papéis encerra sessão e redireciona com mensagem; POST com 0 papéis idem; usuário com papéis mantém fluxo (regressão).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/views.py` | `test_switch_role_zero_roles.py::test_get_zero_roles_logs_out` |
| R2 | `apps/accounts/views.py` | `::test_post_zero_roles_logs_out` |
| R3 | — (regressão) | `uv run pytest apps/accounts/tests/test_active_role.py` — exit 0 |
| R4 | `apps/accounts/tests/test_switch_role_zero_roles.py` | `uv run pytest apps/accounts/tests/test_switch_role_zero_roles.py` |

## RED

- Comando: `uv run pytest apps/accounts/tests/test_switch_role_zero_roles.py`
- Falha esperada: asserts falham — GET em `/switch-role/` com 0 papéis retorna 200 com lista vazia (comportamento atual) em vez de 302 + logout.

## GREEN / verificação local

- `uv run pytest apps/accounts/tests/test_switch_role_zero_roles.py` — exit 0
- `uv run pytest apps/accounts/tests/` — exit 0 (regressão do app, incl. test_active_role.py)
- `uv run ruff check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/views.py
  - apps/accounts/tests/test_switch_role_zero_roles.py
allowed_incidental_files: []
out_of_scope:
  - middleware (EXEMPT_PATHS permanecem; o middleware já cobre paths não isentos)
  - templates (nenhum template novo; mensagem via messages framework)
  - qualquer outro comportamento do change
```

Escale ao parent se: o redirect pós-logout exigir mudança no template de login; houver conflito com testes existentes que não seja simples regressão de expectativa.

## Critérios de aceitação

- [ ] R1–R4 comprovados pelos comandos da matriz
- [ ] Sessão efetivamente encerrada (usuário não autenticado após o redirect)
- [ ] Regressão de test_active_role.py verde (fluxo com papéis intacto)
- [ ] Gate parcial do slice verde
