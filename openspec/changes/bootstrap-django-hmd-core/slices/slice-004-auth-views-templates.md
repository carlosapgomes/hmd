# Slice 004: Autenticação local transitória e templates base

## Objetivo

Fluxo de autenticação local utilizável de ponta a ponta: login/logout com checagem de `account_status`, template base com tema hospitalar, home placeholder por papel e página de perfil. Este mecanismo é **transitório** (ADR-0003): o change `ad-kerberos-authentication` substitui a autenticação de usuários comuns por Kerberos/AD.

## Contexto necessário (contexto zero)

- Slices 001–003 entregues: scaffold + banco + `apps/accounts` com `User`/`Role`/seed.
- Referência somente-leitura: `/projects/dev/ats-web/apps/accounts/views.py` (fluxo login/logout e checagem de `account_status` — o ats-web **não** tem `backends.py`; o backend custom é desenho novo do HMD), `/projects/dev/ats-web/templates/accounts/login.html` (o login fica em `templates/accounts/`, não em `registration/`), `/projects/dev/ats-web/templates/accounts/profile.html` e `/projects/dev/ats-web/static/css/app.css` (tema hospitalar — **adapte a paleta/títulos para HMD**; não copie references de EDA).
- Design: `../design.md` D6 (auth local transitória + break-glass), D9 (templates/tema).
- Spec: `../specs/account-access/spec.md` (autenticação local transitória: login válido, conta bloqueada não autentica).
- Sem e-mails: não implemente reset de senha por e-mail nem convites. A troca de senha local no perfil é a única gestão de senha deste slice.

## Requisitos

- **R1** Backend de autenticação custom que delega a verificação de senha ao mecanismo padrão e **recusa** usuários com `account_status != active` (mensagens de erro genéricas ao usuário).
- **R2** Views + templates: `login` (usuário+senha, mensagem de erro genérica), `logout` (encerra sessão e volta ao login), `profile` (exibe nome, display name, papéis, conselho; permite trocar senha local enquanto a auth local existir).
- **R3** `base.html` com tema hospitalar próprio (navbar: nome do app via `APP_DISPLAY_NAME`, área de mensagens, avatar/nome do usuário, link de perfil/logout), `static/css/app.css` com paleta HMD, responsivo mínimo (Bootstrap 5.3 via CDN).
- **R4** Home autenticada placeholder (`/`): mostra boas-vindas com nome e papéis do usuário; os changes posteriores substituem pelas filas reais. Usuário não autenticado é redirecionado ao login.
- **R5** `config/urls.py` inclui as rotas de `apps.accounts`; `LOGIN_URL`/`LOGIN_REDIRECT_URL`/`LOGOUT_REDIRECT_URL` configurados.
- **R6** Testes de fluxo com client Django: login válido cria sessão; senha errada nega com mensagem; conta bloqueada **e** conta removida negam; logout encerra; profile exige login.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/backends.py` | `test_auth_flow.py::test_blocked_account_cannot_login`, `::test_removed_account_cannot_login` |
| R2 | `apps/accounts/{views,urls,forms?}.py`, `templates/accounts/{login,profile}.html` | `test_auth_flow.py::test_login_logout_profile` |
| R3 | `templates/base.html`, `static/css/app.css` | `test_auth_flow.py::test_home_renders_app_name` + inspeção visual via `curl` em dev |
| R4 | `apps/accounts/views.py` (home), `templates/accounts/home.html` | `test_auth_flow.py::test_home_requires_login` |
| R5 | `config/urls.py`, `config/settings/base.py` | `test_auth_flow.py::test_login_redirects_authenticated` |
| R6 | `apps/accounts/tests/test_auth_flow.py` | `uv run pytest apps/accounts/tests/test_auth_flow.py` |

## RED

- Comando: `uv run pytest apps/accounts/tests/test_auth_flow.py`
- Falha esperada: 404/`NoReverseMatch` — rotas de login/home não existem.

## GREEN / verificação local

- `uv run pytest apps/accounts/tests/test_auth_flow.py` — exit 0
- `uv run pytest apps/accounts/tests/` — exit 0 (regressão de modelo/seed)
- `uv run ruff check . && uv run mypy .` — exit 0
- Manual (opcional, compose dev): login com superusuário do seed renderiza home com `HMD — Hemodinâmica` na navbar.

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/backends.py
  - apps/accounts/views.py
  - apps/accounts/urls.py
  - apps/accounts/forms.py            # login/password change minimal, se usar form
  - templates/base.html
  - templates/accounts/{login,profile,home}.html
  - static/css/app.css
  - config/settings/base.py           # LOGIN_URL etc. + backend em AUTHENTICATION_BACKENDS
  - config/urls.py
  - apps/accounts/tests/test_auth_flow.py
allowed_incidental_files: []
out_of_scope:
  - middleware de papel ativo/switch-role (slice 005) — login redireciona direto p/ home
  - intranet guard (slice 006)
  - reset/convite por e-mail (não existe no HMD — ADR-0003)
  - ícones/favicon PWA (change futuro)
```

Escale ao parent se: o tema exigir assets além de CSS simples; precisar de dependência front-end além de Bootstrap CDN.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz
- [ ] Mensagens de erro de login não revelam motivo interno (genéricas)
- [ ] Conta bloqueada não autentica (R1/R6)
- [ ] Gate parcial do slice verde
