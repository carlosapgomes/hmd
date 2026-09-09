# Slice 002: UI de notificações — badge, lista, abrir/redirect, marcar-todas

## Objetivo

O usuário vê o sino com contagem em toda página, abre a lista com janela de
visibilidade, abre notificação (marca lida + redirect ao caso pelo papel
ativo), marca todas como lidas; endpoint JSON de contagem disponível.

## Contexto necessário

- Slice 001 entregue (`UserNotification` + criação por signal funcionando).
- Design D2 (`openspec/changes/dashboard-notifications-pwa/design.md`).
- ats-web SOMENTE-LEITURA como referência visual/estrutural:
  `apps/accounts/views.py` (notifications_list/open/mark_all_read/
  unread_count), `templates/accounts/notifications.html`,
  `templates/base.html` (sino com badge), `apps/accounts/urls.py`
  (rotas), context processor de contagem — ADAPTE os nomes/URLs ao HMD
  `apps/accounts/urls.py`
  (**NOMES GLOBAIS, SEM NAMESPACE — P1 review**: `apps.accounts.urls` é
  incluído na raiz sem `app_name` e seus nomes (`home`, `profile`, …) são
  globais; NÃO introduza namespace — registre `notifications`,
  `notifications_open`, `notifications_mark_all_read`,
  `notifications_unread_count` como nomes globais e referencie sem
  prefixo, ex. `reverse("notifications")`).
- HMD: papel ativo vem da sessão (`request.session` — veja como views
  existentes leem, ex. `apps/intake/views.py` `active_role`/decorator
  `role_required`; o context processor base expõe `active_role` em
  `templates/base.html`).
- Redirect por papel: rotas `intake:case_detail` (uuid), `doctor:case_detail`,
  `scheduler:case_detail`, `home` fallback (ver `apps/*/urls.py`).
- Settings: padrão de env-driven settings em `config/settings/base.py`
  (`NOTIFICATION_READ_RETENTION_HOURS`, default 48, `.env.example`).
- Template base: `templates/base.html` (área de ações da sessão — inserir
  sino ANTES do perfil, padrão ats-web; ícone Bootstrap `bi-bell`).

## Requisitos verificáveis

- **R1** `visible_for_list()` no model/QuerySet (padrão ats-web:
  `exclude(read_at__lt=cutoff)` com `NOTIFICATION_READ_RETENTION_HOURS`;
  nunca esconde não lidas — NULL não casa `<`).
- **R2** Context processor `apps/accounts/context_processors.py::
  notification_unread_count(request)` → contagem de não lidas do
  autenticado (0 para anônimo), registrado em `TEMPLATES` context_processors
  do `config/settings/base.py`.
- **R3** Views (**nomes de rota GLOBAIS**, sem namespace — P1 review;
  ver Nota de rotas em design.md D2): `notifications` (lista
  `visible_for_list` do `request.user`, ordering já do model);
  `notifications_open` (**POST**; `get_object_or_404(recipient=request.user)`;
  marca `read_at` se nulo; redirect `resolve_notification_redirect_url`);
  `notifications_mark_all_read` (**POST**; update `read_at=now` nas não
  lidas do usuário; redirect à lista); `notifications_unread_count`
  (GET JSON `{"unread_count": N}` do autenticado). Guards: login exigido
  (`LoginRequiredMixin` ou padrão do repo); nada de notificação alheia
  (404). `resolve_notification_redirect_url(case, active_role)` em
  `apps/accounts/notifications.py`: nir→`intake:case_detail`,
  doctor→`doctor:case_detail`, scheduler→`scheduler:case_detail`,
  sem papel/admin→`home` (rotas existentes fazem o guard real do caso).
- **R4** `templates/accounts/notifications.html`: lista (título, preview,
  tempo relativo/hora, indicador de não lida, link-form POST de abrir),
  botão "Marcar todas como lidas" (form POST), estado vazio amigável;
  `templates/base.html`: sino + badge com `{{ notification_unread_count }}`
  linkando a lista.
- **R5** Testes: badge 3/0 (context processor em página autenticada);
  lista com janela (não lida antiga aparece, lida >48h some do resultado
  mas continua no banco — `freeze_time`/`timezone` override do repo);
  abrir marca lida + redirect por papel (nir E doctor no mesmo caso);
  notificação alheia → 404 sem marcar; mark-all zera badge; endpoint JSON
  retorna contagem; anônimo → redirect login.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/models.py` | `test_visible_for_list_retention` |
| R2 | `apps/accounts/context_processors.py`, `config/settings/base.py` | `test_context_processor_count` |
| R3 | `apps/accounts/{views,urls,notifications}.py` | `test_open_marks_read_redirects_nir`, `test_open_doctor_role_redirect`, `test_open_foreign_404`, `test_mark_all_read`, `test_unread_count_json` |
| R4 | `templates/accounts/notifications.html`, `templates/base.html` | `test_navbar_bell_renders_count` |
| R5 | `apps/accounts/tests/test_notification_views.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/models.py            # +visible_for_list
  - apps/accounts/{views,urls}.py      # rotas/views de notificação
  - apps/accounts/notifications.py     # +resolve_notification_redirect_url
  - apps/accounts/context_processors.py
  - apps/accounts/tests/test_notification_views.py
  - templates/accounts/notifications.html
  - templates/base.html                # sino + badge
  - config/settings/base.py            # context processor + retention setting
  - .env.example                       # NOTIFICATION_READ_RETENTION_HOURS

out_of_scope:
  - signal/services do slice 001; dashboard; PWA; manual; apps/cases
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_notification_views.py`
- Falha esperada: `ImportError`/404 — rota global `notifications` inexistente.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/ apps/intake/tests/` — exit 0
- `uv run ruff check apps/accounts config && uv run ruff format --check apps/accounts config`
- `uv run mypy .`

## Critérios de aceitação

- [ ] R1–R5 comprovados; abrir marca lida e leva ao caso pela visão do papel
- [ ] Notificação alheia → 404 (nada marcado); mark-all só do próprio usuário
- [ ] Badge presente em TODA página autenticada (base.html)
- [ ] Gate parcial do slice verde
