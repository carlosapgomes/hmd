# Slice 001 — Painel exclusivo manager/admin (rota 403 + navbar)

## Objetivo

O painel gerencial (`dashboard:home`) passa a ser exclusivo dos papéis ativos
`manager` e `admin`: a **rota** responde 403 para qualquer outro papel
autenticado (guard real, não só menu) e o **link da navbar** só aparece para
esses papéis. Anônimo segue redirecionado ao login.

## Contexto necessário (ler antes de editar)

- `apps/dashboard/views.py` — `home` (~:38-64): hoje `@login_required`
  apenas, docstring do módulo diz "sem role_required — o painel é
  transversal" (comentar a decisão nova).
- `apps/accounts/decorators.py` — `role_required(*allowed_roles)` (:18):
  papel ativo fora → PermissionDenied (403); composição
  `@login_required` + `@role_required(...)` é o padrão das filas (ver
  `apps/doctor/views.py` ou `apps/scheduler/views.py` para o molde exato de
  imports/ordem dos decorators).
- `templates/base.html:46-51` — bloco do Painel: hoje
  `{% if user.is_authenticated %}`; molde do gate por papel está nos blocos
  vizinhos (fila médica usa `active_role == 'doctor' or active_role == 'admin'`).
- `apps/dashboard/tests/test_views.py` — testes existentes do painel
  (usuários que usam e o teste `:133` "Unidade 1"); conferir QUEM loga nesses
  testes — se logam com papel não manager/admin, precisam ser ajustados para
  manager/admin (edição necessária e esperada; não é regressão).
- `openspec/changes/painel-gerencial-e-home/design.md` — D1.

## Requisitos verificáveis

- **R1** — `dashboard:home` com `@role_required("manager", "admin")`
  (composição com `@login_required` no padrão das filas): `manager`/`admin`
  → 200 com métricas; `nir`/`doctor`/`scheduler` ativos → 403; anônimo →
  redirect login.
- **R2** — Navbar: link Painel presente com papel ativo `manager`/`admin`;
  ausente para `nir`/`doctor`/`scheduler` e para anônimo.
- **R3** — Testes existentes do dashboard ajustados para logar com papel
  válido (`manager` ou `admin`) — sem perda de cobertura (período, unidades,
  zero-PHI seguem testados); docstrings que citam "transversal" atualizadas.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/dashboard/views.py` | novos: `test_dashboard_ok_for_manager`/`_admin` (200), `test_dashboard_403_for_other_roles` (parametrizado nir/doctor/scheduler), `test_dashboard_anonymous_redirects` |
| R2 | `templates/base.html` | novos: navbar com link p/ manager/admin; sem link p/ nir/doctor/scheduler (render de home/dashboard como anônimo não mostra) |
| R3 | `apps/dashboard/tests/test_views.py` | suíte do dashboard verde com os ajustes de papel |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/dashboard/views.py
  - templates/base.html
  - apps/dashboard/tests/test_views.py

allowed_incidental_files: []
# (home dispatcher é o slice 002 — NÃO tocar em accounts/views.py aqui)

out_of_scope:
  - apps/accounts/views.py (home_view — slice 002)
  - métricas/templates do dashboard (só o acesso muda)
  - notificações/resolve_notification_redirect_url
  - specs de outros apps
```

Escalamento: se algum teste FORA de `apps/dashboard/tests/` navegar ao painel
com papel não gerencial (grep `dashboard:home` em tests/), **pare e reporte**.

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/dashboard -q`
- falha esperada: os testes NOVOS de 403/parametrizados falham (rota hoje é
  livre); os existentes podem quebrar SOMENTE se logavam com papel não
  gerencial — ajuste-os (R3) antes do GREEN.

### GREEN

- mesmo comando — 0 failed.

### Verificação do slice

- `TEST_DB_PORT=55435 uv run pytest apps/dashboard apps/accounts -q` — 0 failed
- `rg -n "Painel" templates/base.html` — bloco com gate manager/admin
- `uv run ruff check apps/dashboard templates 2>/dev/null; uv run ruff check apps/dashboard && uv run ruff format --check apps/dashboard` — ok
- `uv run mypy apps` — ok

## Critérios de aceitação

- [ ] R1–R3 verdes conforme a matriz
- [ ] Guard na ROTA (não só menu), no padrão das filas
- [ ] RED demonstrado antes do GREEN (mesmo comando)
- [ ] Nenhum arquivo fora do blast radius
