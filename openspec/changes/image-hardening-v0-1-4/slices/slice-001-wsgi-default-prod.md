# Slice 001 — WSGI fail-safe: default `config.settings.prod`

## Objetivo

O entrypoint WSGI (`config/wsgi.py`) passa a assumir **settings de produção**
na ausência de `DJANGO_SETTINGS_MODULE` — a env explícita continua vencendo.
Esquecer a env deixa de levantar o site com configuração de desenvolvimento.

## Contexto necessário (ler antes de editar)

- `config/wsgi.py` — arquivo inteiro (5 linhas; o default atual é
  `config.settings.dev`).
- `manage.py` — default próprio (DEV; **não muda** neste slice — design D2).
- `apps/accounts/tests/test_prod_secrets.py` — padrão dos testes de
  subprocess com settings de prod (envs dummy + secret em arquivo;
  `_create_secret_dummies`, `_set_minimal_prod_env`) e dos checks estáticos
  de compose; é o lar natural da regressão nova.
- `openspec/changes/image-hardening-v0-1-4/design.md` — D2.
- Cenário novo da spec: "WSGI sem variável de ambiente cai em produção".

## Requisitos verificáveis

- **R1** — `config/wsgi.py` usa
  `os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")`.
- **R2** — Regressão (subprocess): sem `DJANGO_SETTINGS_MODULE` no ambiente,
  importar `config.wsgi` carrega `config.settings.prod` (assert do
  `django.conf.settings.SETTINGS_MODULE`); com a env setada (ex.:
  `config.settings.test`), a env vence. O subprocess precisa das envs mínimas
  de prod (secret dummy em arquivo + `DATABASE_URL`) OU usar uma env
  alternativa para provar a precedência — escolha o caminho mais simples que
  exercite o `setdefault` de verdade (import real do módulo, sem mock).
- **R3** — Nenhum outro arquivo muda de comportamento: `manage.py` mantém o
  default dev; suite existente segue verde sem edição (o runserver de
  desenvolvimento não passa pelo wsgi default).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `config/wsgi.py` | inspeção/diff (1 linha) |
| R2 | `apps/accounts/tests/test_prod_secrets.py` | `test_wsgi_defaults_to_prod_settings_without_env` + `test_wsgi_env_overrides_default` (subprocess) |
| R3 | — | `TEST_DB_PORT=55435 uv run pytest apps/accounts -q` sem edição de testes existentes |

## Escopo e expected blast radius

```yaml
expected_files:
  - config/wsgi.py
  - apps/accounts/tests/test_prod_secrets.py

allowed_incidental_files: []

out_of_scope:
  - manage.py (default dev preservado)
  - Dockerfile/compose/release (slices 002/003)
  - settings de dev/test/prod
```

Escalamento: se algum teste existente depender do default dev do wsgi (grep
`config.wsgi` em tests/ e apps/), **pare e reporte**.

## Notas de implementação

- Subprocess no padrão dos existentes: `[sys.executable, "-c", "..."]` com
  `env` controlado (`env.pop("DJANGO_SETTINGS_MODULE", None)`) — não edite
  `os.environ` do processo de teste.
- Para o import de prod funcionar no subprocess são necessárias as envs
  mínimas (secret em arquivo dummy + `DATABASE_URL`) — reutilize os helpers.

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_prod_secrets.py -q`
- falha esperada: `test_wsgi_defaults_to_prod_settings_without_env` falha —
  o wsgi atual carrega `config.settings.dev` sem a env.

### GREEN

- mesmo comando — 0 failed.

### Verificação do slice

- `TEST_DB_PORT=55435 uv run pytest apps/accounts -q` — 0 failed
- `uv run ruff check config/wsgi.py apps/accounts/tests/test_prod_secrets.py && uv run ruff format --check config/wsgi.py apps/accounts/tests/test_prod_secrets.py` — ok
- `uv run mypy config apps/accounts` — ok

## Critérios de aceitação

- [ ] R1–R3 verdes conforme a matriz
- [ ] RED demonstrado antes do GREEN (mesmo comando)
- [ ] `manage.py` intocado; nenhum arquivo fora do blast radius
