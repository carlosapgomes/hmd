# Slice 001: Runtime prod — gunicorn/collectstatic + healthz/readyz + settings

## Objetivo

Imagem de produção servível: gunicorn como CMD com collectstatic no build;
endpoints públicos `/healthz` (liveness) e `/readyz` (readiness com ping de
DB, sem dados); settings de produção com `DatabaseCache` tabela `hmd_cache`,
`CSRF_TRUSTED_ORIGINS` e `SECURE_PROXY_SSL_HEADER` env-driven.

## Contexto necessário

- Design D1/D2/D3 (`openspec/changes/pilot-deployment-v0-1-1/design.md`).
- `config/settings/prod.py` (guard anti-LocMem em :29-35 — MANTER;
  ALLOWED_HOSTS :36-38; WhiteNoise manifest `prod.py:97-103`).
- `config/settings/base.py` (padrão de env bool dos flags —
  `INTAKE_RUN_TASKS_INLINE` etc.; `STATIC_ROOT` :185).
- `Dockerfile` (build deps+modelo; `COPY . /app`; EXPOSE 8000; hoje SEM
  CMD). `.dockerignore` (staticfiles/ excluído — o collectstatic do BUILD
  entra pela camada RUN, não pelo COPY).
- `apps/accounts/urls.py` (rotas globais sem namespace; views em
  `apps/accounts/views.py`) + `middleware.py` `EXEMPT_PATHS` (adicionar
  healthz/readyz para o IntranetGuard não 403 nelas).
- Views de health: crie `apps/accounts/views_health.py` (contas é o app do
  shell/login) com as duas views puras; urls globais `healthz`/`readyz`.
- Tests: `apps/accounts/tests/` (padrões; readyz precisa de DB — usar mock
  de `connections` para o 503 sem matar o teste: monkeypatch do cursor).

## Requisitos verificáveis

- **R1** `gunicorn>=23,<24` em `pyproject.toml` `dependencies` (uv lock
  atualizado: `uv lock` + `uv sync`).
- **R2** Dockerfile: passo `collectstatic` no build (envs dummy
  `DJANGO_SECRET_KEY=build-dummy` `DATABASE_URL=postgres://build:build@localhost/build`,
  `--settings=config.settings.prod`, `--noinput`) APÓS o `COPY . /app`; e
  `CMD` gunicorn (bind 0.0.0.0:8000, 3 workers, 2 threads, timeout 60,
  graceful 30, capture-output, access/error logfile "-").
- **R3** `/healthz`: 200 JSON `{"status": "ok"}` sem auth, sem DB, sem
  dados; **`/readyz`**: 200 `{"status": "ready"}` com `SELECT 1` via
  `django.db.connection`; 503 `{"status": "unavailable"}` em falha; ambas
  em `EXEMPT_PATHS` do IntranetGuard; públicas (sem login).
- **R4** prod.py: `CACHES` default `DatabaseCache` LOCATION `hmd_cache`
  (guard LocMem mantido — LocMem continua proibido);
  `CSRF_TRUSTED_ORIGINS` env (default `https://hmd.projetoshgrs.com`,
  split por vírgula); `SECURE_PROXY_SSL_HEADER` =
  `("HTTP_X_FORWARDED_PROTO", "https")` quando env `PROXY_SSL_HEADER`
  (default "true") ligado, senão None.
- **R5** Tests: healthz 200 anônimo; readyz 200 com DB (django_db); readyz
  503 com cursor falho (monkeypatch); exempt do guard (papel nir de IP
  não-intranet acessa healthz sem 403); CSRF origins default e override;
  cache prod é DatabaseCache/hmd_cache (import de settings com envs mínimos
  — cuidado: importar prod exige DJANGO_SECRET_KEY dummy).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) | Teste/check |
| --- | --- | --- |
| R1 | `pyproject.toml`, `uv.lock` | `uv sync` ok; import gunicorn |
| R2 | `Dockerfile` | revisão por leitura (+ `docker build` opcional se rápido) |
| R3 | `apps/accounts/views_health.py`, `apps/accounts/urls.py`, `apps/accounts/middleware.py` | `test_healthz_*`, `test_readyz_*`, `test_health_exempt_from_guard` |
| R4 | `config/settings/prod.py` | `test_prod_cache_database`, `test_prod_csrf_defaults` |
| R5 | `apps/accounts/tests/test_health.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - pyproject.toml
  - uv.lock
  - Dockerfile
  - apps/accounts/views_health.py
  - apps/accounts/urls.py
  - apps/accounts/middleware.py          # EXEMPT_PATHS
  - config/settings/prod.py
  - apps/accounts/tests/test_health.py

out_of_scope:
  - compose prod (003); workflow (004); intake lock (002)
  - gunicorn config file (CLI flags bastam); wsgi.py (existe)
```

## Plano de testes do slice

### RED

- `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_health.py` →
  404/NoReverseMatch (rotas inexistentes).

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/ apps/intake/tests/`
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .`

## Critérios de aceitação

- [ ] R1–R5 comprovados; health/ready sem dados de negócio e sem auth
- [ ] Dockerfile com CMD gunicorn + collectstatic de build (sem secret real)
- [ ] Guard anti-LocMem intacto; cache prod = hmd_cache
- [ ] Gate parcial do slice verde
