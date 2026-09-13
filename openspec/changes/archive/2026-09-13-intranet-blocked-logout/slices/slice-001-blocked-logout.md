# Slice 001 — Logout no bloqueio + página com retorno ao login

## Objetivo

Quando o `IntranetGuardMiddleware` bloqueia um usuário exclusivamente
restrito de fora da intranet, ele passa a: (1) **encerrar a sessão**
(`logout(request)`), (2) responder 403 com **template próprio** contendo a
mensagem atual e um botão "Voltar ao login" (`/login/`). O usuário nunca mais
fica preso com cookie vivo e sem rota de saída.

## Contexto necessário (ler antes de editar)

- `apps/accounts/middleware.py` — o ramo de bloqueio (~136-145: `logger.warning(...)`
  seguido de `HttpResponseForbidden(INTRANET_BLOCKED_MESSAGE)`); a constante
  em `:40`. O docstring da classe (regras 1-7) menciona "HTTP 403 com
  mensagem clara" — atualizar para refletir logout + página.
- `templates/accounts/login.html` — molde de template do app (extends
  `base.html`, `{% block content %}`); o link de login é
  `{% url 'login' %}`.
- `apps/accounts/views.py:login_view` — autenticado → `redirect(home)` (por
  isso o logout ANTES do render é essencial: anônimo → form).
- `config/settings/base.py` MIDDLEWARE — ordem: SessionMiddleware precede o
  guard (o cookie de sessão é derrubado na volta da resposta; design D3).
- `apps/accounts/tests/test_intranet_guard.py` — `test_nir_blocked_outside_range`
  (afirma 403 + fragmento da mensagem) será estendido; helpers
  `_create_user`/`_login_with_active_role`.
- `openspec/changes/intranet-blocked-logout/design.md` — D1-D5 (D2: template
  estende base.html e recebe `INTRANET_BLOCKED_MESSAGE` via context — SEM
  texto duplicado no HTML).

## Requisitos verificáveis

- **R1** — Bloqueio externo de usuário exclusivamente restrito: resposta **403**
  renderizando `templates/accounts/intranet_blocked.html` com a mensagem atual
  e link para `{% url 'login' %}` ("Voltar ao login").
- **R2** — A sessão é encerrada NO bloqueio: após a resposta, `client.session`
  não contém `_auth_user_id`/`active_role` e um GET subsequente a qualquer view
  protegida redireciona ao login (anônimo) em vez de 403 de novo.
- **R3** — O botão funciona ponta a ponta: GET ao link presente no corpo →
  200 com formulário de login (não redirect para home).
- **R4** — Regras vizinhas intocadas: multi-role com papel externo passa SEM
  logout (sessão preservada); paths isentos e IP de intranet seguem como são;
  conjunto todo restrito ({nir, scheduler}) também ganha logout+403 (mesma
  classe de usuário).
- **R5** — Log: linha `intranet_guard_blocked … session_terminated=1` (pk,
  sem username/CPF); docstring da classe atualizado; nenhum outro comportamento
  do middleware muda.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/middleware.py`, `templates/accounts/intranet_blocked.html` | `test_nir_blocked_outside_range` estendido: 403 + `reverse("login")` no corpo + mensagem |
| R2 | `apps/accounts/middleware.py` | `test_blocked_session_is_terminated` (novo): pós-resposta sem `_auth_user_id`/`active_role`; GET a `/` → redirect login |
| R3 | template | `test_blocked_page_login_link_works` (novo): GET ao link → 200 + form (não redirect) |
| R4 | `apps/accounts/middleware.py` | `test_multi_role_with_external_role_passes_with_active_nir` (existente, sem edição — sessão preservada implícita) + `test_all_roles_restricted_stays_blocked` estendido se necessário (assert logout) |
| R5 | `apps/accounts/middleware.py` | inspeção: `session_terminated=1` no log; docstring atualizado |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/middleware.py
  - templates/accounts/intranet_blocked.html
  - apps/accounts/tests/test_intranet_guard.py

allowed_incidental_files: []

out_of_scope:
  - views de login/logout (inalteradas), rotas, settings
  - regra de bloqueio em si (conjunto de papéis — change anterior, encerrado)
  - lockout/ratelimit, backends de auth
  - manual/PWA/templates de base
```

Escalamento: se o render no middleware exigir `RequestContext`/processors que
não estejam disponíveis no caminho de middleware (ex.: context processor que
acessa DB e falha para anônimo), **pare e reporte** — não reestruture.

## Notas de implementação

- `logout(request)` (import de `django.contrib.auth`) imediatamente após o
  `logger.warning`, antes do render; use `render(request, ...)` (não
  `render_to_string`) para o context processar normalmente.
- Context mínimo: `{"message": INTRANET_BLOCKED_MESSAGE}`; o template usa
  `{% url 'login' %}` — sem URL hardcoded.
- Template: extends `base.html`, `{% block content %}` com um card central,
  título = `{{ message }}`, linha de apoio ("Acesse pela rede interna do
  hospital; sua sessão foi encerrada."), `<a class="btn btn-primary"
  href="{% url 'login' %}">Voltar ao login</a>`. Sem formulários (sem CSRF).
- Retorno permanece `HttpResponseForbidden(render(...))` (status 403).

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_intranet_guard.py -q`
- falha esperada: `test_blocked_session_is_terminated` e
  `test_blocked_page_login_link_works` FALHAM no código atual (sessão
  sobrevive ao 403; corpo não contém link de login), e o
  `test_nir_blocked_outside_range` estendido falha no assert do link.

### GREEN

- mesmo comando — 0 failed (incluindo os existentes de isenção/proxy/faixa
  vazia, sem edição).

### Verificação do slice

- `TEST_DB_PORT=55435 uv run pytest apps/accounts -q` — 0 failed
- `uv run ruff check apps/accounts/middleware.py apps/accounts/tests/test_intranet_guard.py && uv run ruff format --check apps/accounts/middleware.py apps/accounts/tests/test_intranet_guard.py` — ok
- `uv run mypy apps/accounts` — ok
- Suíte completa fica para o gate final do change.

## Critérios de aceitação

- [ ] R1–R5 verdes conforme a matriz
- [ ] RED demonstrado antes do GREEN (mesmo comando)
- [ ] Multi-role externo segue sem logout (R4: teste existente intocado passa)
- [ ] Nenhum arquivo fora do blast radius
