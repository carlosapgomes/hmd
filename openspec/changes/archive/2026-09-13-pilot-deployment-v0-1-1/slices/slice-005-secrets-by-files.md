# Slice 005: Alias estável + segredos por arquivos (v0.1.2)

## Objetivo

Correções obrigatórias do blueprint (Eon/owner): (1) alias estável `hmd` do
web em `hospital_ingress_hmd` (upstream do Caddy sempre `hmd:8000`);
(2) TODOS os segredos por arquivos read-only, consumidor por segredo, sem
senha em env/`DATABASE_URL`; (3) `latest` fora dos releases futuros;
(4) bump v0.1.2 (settings/imagem mudam).

## Contexto necessário

- `config/settings/db.py::database_config` (JÁ suporta `DB_*` +
  `DB_PASSWORD_FILE` com precedência de arquivo e fail-closed nomeado —
  USE; hoje `prod.py` usa `dj_database_url.config`/`DATABASE_URL`);
- `config/settings/prod.py` (SECRET_KEY env hoje; guard do cache;
  ALLOWED_HOSTS/CSRF/proxy prontos);
- `apps/accounts/management/commands/seed_admin.py`
  (DJANGO_SUPERUSER_USERNAME/PASSWORD por env hoje);
- `docker-compose.prod.yml` (estrutura atual: web/migrate/workers, redes
  externas, healthcheck, defaults `${VAR:-...}`);
- `.github/workflows/release.yml` (metadata com `type=raw,value=latest`);
- README seção "Piloto (fase 1)" (menciona MIGRATOR_DATABASE_URL/hmd-prod-web-1);
- `.gitignore`.

## Requisitos verificáveis

- **R1** prod.py: `DATABASES = database_config(os.environ,
  conn_max_age=600, conn_health_checks=True)` (import de
  `config.settings.db`) — `DATABASE_URL` deixa de ser o caminho do compose
  (a função mantém precedência de URL se alguém a setar em dev);
  `SECRET_KEY` lê `DJANGO_SECRET_KEY_FILE` (arquivo com precedência sobre
  a env `DJANGO_SECRET_KEY`; arquivo ilegível/vazio → ImproperlyConfigured
  nomeado; nenhuma fonte → o erro atual).
- **R2** seed_admin: aceita `DJANGO_SUPERUSER_PASSWORD_FILE` (mesma
  semântica de precedência/fail-closed; username segue por env
  não-sensível `DJANGO_SUPERUSER_USERNAME`).
- **R3** compose: `secrets:` top-level — `app_db_password`
  (file `${APP_DB_PASSWORD_FILE:-./secrets/app_db_password.txt}`),
  `migrator_db_password` (`${MIGRATOR_DB_PASSWORD_FILE:-./secrets/migrator_db_password.txt}`),
  `secret_key` (`${SECRET_KEY_FILE:-./secrets/secret_key.txt}`),
  `superuser_password` (`${SUPERUSER_PASSWORD_FILE:-./secrets/superuser_password.txt}`);
  montagens: `secret_key`+`app_db_password` → web/workers; `secret_key`+
  `migrator_db_password`+`superuser_password` → migrate; NUNCA um segredo
  em serviço que não o consome. Env DB dos serviços:
  `DB_HOST=${DB_HOST}`, `DB_PORT/DB_NAME/DB_USER` idem (web/workers =
  role da aplicação; migrate = `DB_USER=${MIGRATOR_DB_USER}` +
  `DB_PASSWORD_FILE=/run/secrets/migrator_db_password`;
  web/workers usam `/run/secrets/app_db_password`) +
  `DJANGO_SECRET_KEY_FILE=/run/secrets/secret_key` em TODOS; migrate:
  `DJANGO_SUPERUSER_PASSWORD_FILE=/run/secrets/superuser_password`.
  REMOVE `DATABASE_URL`/`MIGRATOR_DATABASE_URL` do compose inteiro.
- **R4** compose: web ganha `networks.hospital_ingress_hmd.aliases:
  ["hmd"]`; README: upstream do Caddy SEMPRE `hmd:8000` (remover
  hmd-prod-web-1); runbook: imagem pinada tag+digest
  (`HMD_IMAGE_TAG=v0.1.2@sha256:<digest>` obrigatório no .env do host);
  `.env.example`: nomes novos (caminhos de arquivos de segredos + DB_HOST
  etc.), SEM values; `.gitignore` += `secrets/`.
- **R5** workflow: remove `type=raw,value=latest` (releases futuros só a
  tag); CHANGELOG `[0.1.2]` + `pyproject/uv.lock` 0.1.2; compose default
  `HMD_IMAGE_TAG:-v0.1.2`.
- **R6** Testes: SECRET_KEY por arquivo com precedência (env+arquivo →
  arquivo vence) e fail-closed (sem fonte → ImproperlyConfigured); prod
  DATABASES via `DB_*`+`DB_PASSWORD_FILE` SEM `DATABASE_URL` no env (e o
  erro nomeado quando falta senha+arquivo — pode já existir em testes do
  db.py; reaproveite); seed_admin com `DJANGO_SUPERUSER_PASSWORD_FILE`
  (tmp_path) e fail-closed; compose `config --quiet` com TODOS os profiles
  + arquivos dummy de segredo criados em tmp (documente o comando no
  relatório).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) | Teste/check |
| --- | --- | --- |
| R1 | `config/settings/prod.py` | `test_prod_secret_key_file_precedence`, `test_prod_secret_key_fail_closed`, `test_prod_db_components_without_url` |
| R2 | `apps/accounts/management/commands/seed_admin.py` | `test_seed_admin_password_file` |
| R3/R4 | `docker-compose.prod.yml`, `.env.example`, `.gitignore`, `README.md` | `compose config --quiet` (todos os profiles, dummies); grep sem `DATABASE_URL`/`hmd-prod-web-1` |
| R5 | `.github/workflows/release.yml`, `CHANGELOG.md`, `pyproject.toml`, `uv.lock` | grep sem `value=latest`; version 0.1.2 |
| R6 | `apps/accounts/tests/test_prod_secrets.py` (ou config/tests existente) | suíte |

## Escopo e expected blast radius

```yaml
expected_files:
  - config/settings/prod.py
  - apps/accounts/management/commands/seed_admin.py
  - apps/accounts/tests/test_prod_secrets.py   # ou local equivalente
  - docker-compose.prod.yml
  - .env.example
  - .gitignore
  - README.md
  - .github/workflows/release.yml
  - CHANGELOG.md
  - pyproject.toml
  - uv.lock

out_of_scope:
  - apagar tag latest existente no registry; mover v0.1.1; código de domínio
  - config/settings/db.py (já suporta tudo — NÃO editar)
```

## Plano de testes do slice

### RED
- `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_prod_secrets.py`
  → falha (suporte _FILE inexistente).

### GREEN / verificação local
- Suíte completa + ruff/format/mypy
- `mkdir -p /tmp/hmd-secrets && echo x > ...` dummies +
  `docker compose --profile migrate --profile workers -f docker-compose.prod.yml
  --env-file .env.example config --quiet` (aponte os caminhos _FILE do
  env p/ os dummies)

## Critérios de aceitação
- [ ] Nenhuma senha/valor secreto em env do compose; um consumidor por segredo
- [ ] Alias `hmd`; docs só `hmd:8000`; runbook exige tag+digest
- [ ] `latest` fora do workflow; v0.1.2 coerente
- [ ] Tests de precedência/fail-closed + compose config verde
