# Slice 002 — Home por papel ativo + ordem do menu NIR

## Objetivo

A home (`home_view`) deixa de ser placeholder universal e passa a
**redirecionar cada papel ativo à sua área de trabalho**: `nir` →
`intake:home` (Enviar relatório — o caso de uso mais comum), `doctor` →
`doctor:queue`, `scheduler` → `scheduler:queue`, `manager`/`admin` →
`dashboard:home`; sem papel ativo válido → placeholder (fallback). O menu
NIR troca de ordem: `[Enviar relatório] [Meus casos]`.

## Contexto necessário (ler antes de editar)

- `apps/accounts/views.py:home_view` (~:171-179) — hoje renderiza
  `accounts/home.html` com `role_names`; vira dispatcher de redirects
  (design D2: tabela papel→rota; `ActiveRoleMiddleware` já garante
  active_role válido).
- `templates/accounts/home.html` — fallback: atualizar o texto que promete
  "as filas do seu papel substituirão esta tela nas próximas versões"
  (design D5 — a substituição aconteceu; fallback orienta trocar papel/sair).
- `templates/base.html:27-33` — bloco NIR: swap de ordem dos dois links.
- `apps/accounts/tests/test_active_role.py` e testes de auth flow
  (`test_auth_flow.py`) — conferir quem navega à `home` após login e o que
  espera (redirects novos podem exigir ajustes pontuais — edição esperada se
  e somente se o teste assertava o placeholder pós-login de papel com fila).
- `apps/accounts/notifications.py` — `resolve_notification_redirect_url`:
  NÃO MUDA (design D3); apenas CONFIRMAR por leitura que o destino `home`
  para manager/admin continua coerente (agora aterrissa no painel).
- `openspec/changes/painel-gerencial-e-home/design.md` — D2-D5.

## Requisitos verificáveis

- **R1** — `home_view` redireciona (302) por papel ativo: nir→`intake:home`,
  doctor→`doctor:queue`, scheduler→`scheduler:queue`,
  manager/admin→`dashboard:home` (parametrizado; assert do `Location`).
- **R2** — Sem papel ativo na sessão → placeholder 200 (fallback), sem redirect.
- **R3** — Pós-login o fluxo ponta a ponta funciona: login de usuário nir →
  cai em `intake:home` (via redirect da home), com form de envio renderizado.
- **R4** — Menu NIR: `Enviar relatório` aparece ANTES de `Meus casos` no HTML
  (assert de índices no corpo renderizado).
- **R5** — Notificações intocadas: teste existente de
  `resolve_notification_redirect_url` segue verde sem edição (design D3).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/views.py` | `test_home_redirects_per_active_role` (novo, parametrizado 5 papéis × 4 destinos) |
| R2 | `apps/accounts/views.py` | `test_home_without_active_role_shows_fallback` (novo: 200 + texto de boas-vindas) |
| R3 | — | `test_login_nir_lands_on_intake_home` (novo: fluxo login→home→redirect→form) |
| R4 | `templates/base.html` | `test_nir_nav_order_send_report_first` (novo: índice de "Enviar relatório" < índice de "Meus casos" no HTML de uma página nir) |
| R5 | — (sem edição) | suíte de notificações verde sem edição |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/views.py
  - templates/base.html
  - templates/accounts/home.html
  - apps/accounts/tests/test_home_dispatch.py   # novo — ou no arquivo de auth flow existente, se couber naturalmente
  - apps/accounts/tests/test_active_role.py     # APENAS se testes existentes assertarem placeholder pós-login

allowed_incidental_files:
  # DESVIO AUTORIZADO pelo supervisor (escalamento do slice; opção A):
  # 12 arquivos de teste, edição TEST-ONLY — follow=True onde o teste asserta
  # navbar/corpo após GET à home (o dispatcher transforma a home em 302) +
  # retarget aprovado do teste do placeholder p/ papel fora da tabela.
  - apps/accounts/tests/test_active_role.py
  - apps/accounts/tests/test_auth_flow.py
  - apps/accounts/tests/test_intranet_guard.py
  - apps/accounts/tests/test_kerberos_backend.py
  - apps/accounts/tests/test_manual.py
  - apps/accounts/tests/test_notification_views.py
  - apps/accounts/tests/test_pwa.py
  - apps/dashboard/tests/test_views.py
  - apps/doctor/tests/test_queue.py
  - apps/intake/tests/test_my_cases.py
  - apps/scheduler/tests/test_views.py

out_of_scope:
  - dashboard (slice 001 encerrado), métricas, guards de rota
  - notifications.py (sem edição — só leitura)
  - intake/doctor/scheduler (rotas de destino intocadas)
  - manual/PWA
```

Escalamento: se testes FORA de accounts dependerem da home renderizando o
placeholder com papel ativo definido (grep `reverse("home")`/`'home'` em
apps/*/tests), **pare e reporte** — não edite outros apps.

## Notas de implementação

- Dispatcher como dict módulo-nível `_HOME_ROUTES: dict[str, str]` (papel →
  nome de rota) + `redirect(reverse(...))`; sem if-cadeia verbosa.
- `home_view` continua `@login_required`; `_require_user` permanece para o
  fallback.
- Não cacheie nada; redirect puro.

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/accounts -q`
- falha esperada: os testes NOVOS de redirect/ordem falham (home renderiza
  placeholder para todos; menu NIR na ordem antiga).

### GREEN

- mesmo comando — 0 failed (incluindo notificações/auth flow sem edição,
  salvo ajustes previstos em R5/contexto).

### Verificação do slice

- `TEST_DB_PORT=55435 uv run pytest apps/accounts apps/dashboard -q` — 0 failed
- `uv run ruff check apps/accounts && uv run ruff format --check apps/accounts` — ok
- `uv run mypy apps` — ok
- Suíte completa fica para o gate final do change.

## Critérios de aceitação

- [ ] R1–R5 verdes conforme a matriz
- [ ] RED demonstrado antes do GREEN (mesmo comando)
- [ ] `resolve_notification_redirect_url` sem edição (R5)
- [ ] Nenhum arquivo fora do blast radius
