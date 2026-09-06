# Slice 001: Scaffold Django + toolchain + documentação viva

## Objetivo

Criar o repositório executável do HMD: projeto Django 5.2 SSR mínimo (`config/`), toolchain de qualidade completa (`uv`, ruff, mypy, pytest) e documentação viva (AGENTS.md, PROJECT_CONTEXT.md, README, ADRs 0001–0003). Ao final, o quality gate roda verde com smoke tests — sem banco de dados, sem apps de domínio.

## Contexto necessário (contexto zero)

- Projeto HMD: clone fresh dos **padrões** do `ats-web` (monolito Django SSR para triagem médica). Não copie código às cegas: leia o equivalente e adapte ao HMD.
- Referência somente-leitura (mesma máquina): `/projects/dev/ats-web/` — veja `pyproject.toml`, `conftest.py`, `config/settings/{base,dev,prod,test,db}.py`, `config/urls.py`, `AGENTS.md` (estrutura e seções).
- Decisões deste change: `openspec/changes/bootstrap-django-hmd-core/design.md` (D1–D4, D9, D10). Leia antes de escrever.
- Nomes: app de exibição `HMD — Hemodinâmica` (env `APP_DISPLAY_NAME`); settings em `config.settings.{base,dev,prod,test}`; sem e-mails, sem django-fsm, sem django-q2, sem apps de domínio neste slice.

## Requisitos

- **R1** `uv run pytest` executa smoke tests que provam: settings carregam, `APP_DISPLAY_NAME` = "HMD — Hemodinâmica", `ROOT_URLCONF` resolvível e uma URL simples responde 200 (health da home estática mínima).
- **R2** `uv run ruff check .` e `uv run ruff format --check .` terminam exit 0 (config em `pyproject.toml`; linha 100; alvo py313).
- **R3** `uv run mypy .` termina exit 0 com `django-stubs` + plugin configurados (código inicial já tipado; `config/` e `tests/` no escopo).
- **R4** `uv run python manage.py check --settings=config.settings.dev` executa sem erros.
- **R5** Existem e estão preenchidos: `AGENTS.md` (stack, comandos, política de testes, workflow OpenSpec com slices verticais/TDD, política de commit), `PROJECT_CONTEXT.md` (fontes autoritativas, objetivo do sistema, roadmap dos 11 changes resumido), `README.md` atualizado, `docs/adr/ADR-0001..0003` (conteúdo conforme design D1, D5, D6), `.env.example` com todas as vars de settings documentadas.
- **R6** `config/settings/prod.py` falha imediatamente (ImproperlyConfigured) sem `DJANGO_SECRET_KEY`; `dev` usa default explícito de desenvolvimento.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `config/**`, `tests/test_smoke.py`, `conftest.py`, `pyproject.toml` | `uv run pytest tests/test_smoke.py` |
| R2 | `pyproject.toml` (seção ruff) | `uv run ruff check . && uv run ruff format --check .` |
| R3 | `pyproject.toml` (mypy + django-stubs), código tipado | `uv run mypy .` |
| R4 | `manage.py`, `config/settings/dev.py` | comando de R4, exit 0 |
| R5 | `AGENTS.md`, `PROJECT_CONTEXT.md`, `README.md`, `docs/adr/ADR-000{1,2,3}*.md`, `.env.example` | inspeção: seções/ADRs presentes |
| R6 | `config/settings/{prod,base}.py` | `tests/test_smoke.py::test_prod_requires_secret_key` |

## RED

- Comando: `uv run pytest tests/test_smoke.py`
- Falha esperada: collection error/import error — projeto Django ainda não existe (prova que o comportamento de R1/R6 não existe).

## GREEN / verificação local

- `uv run pytest tests/test_smoke.py` — exit 0
- `uv run ruff check . && uv run ruff format --check .` — exit 0
- `uv run mypy .` — exit 0
- `uv run python manage.py check --settings=config.settings.dev` — exit 0
- `rg -n "APP_DISPLAY_NAME" config/ .env.example` — presente nas settings e documentado

## Escopo e expected blast radius

```yaml
expected_files:
  - pyproject.toml          # deps + ruff + mypy + pytest config
  - uv.lock
  - manage.py
  - config/settings/{__init__,base,dev,prod,test}.py
  - config/{__init__,urls,wsgi,asgi}.py
  - conftest.py
  - tests/test_smoke.py
  - templates/ + static/css/app.css   # mínimo p/ home estática responder 200
  - AGENTS.md / PROJECT_CONTEXT.md / README.md / .env.example
  - docs/adr/ADR-0001..0003*.md
allowed_incidental_files:
  - .gitignore (ajustes)
  - config/settings/db.py (stub mínimo, detalhado no slice 002)
out_of_scope:
  - docker-compose (slice 002)
  - apps/ de domínio e AUTH_USER_MODEL custom (slice 003)
  - login/views de conta (slice 004)
```

Escalone ao parent (não amplie) se: precisar de app Django além do mínimo para a home; precisar de banco de dados; conflito com decisão de design.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz
- [ ] Quality gate parcial do slice verde (comandos de GREEN)
- [ ] Nenhum arquivo fora do blast radius
- [ ] Documentação viva utilizável por implementador com contexto zero
