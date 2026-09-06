# Slice 002: Banco de dados e ambientes compose

## Objetivo

Tornar o ambiente executável de ponta a ponta: resolução de configuração de banco (`DATABASE_URL` com precedência sobre `DB_*`, suporte a `DB_PASSWORD_FILE`) testada como função pura, e composições Docker para dev (PostgreSQL 17 + web) e teste (PostgreSQL efêmero). Ao final, `migrate` aplica no compose dev e `pytest` roda contra banco de teste dedicado.

## Contexto necessário (contexto zero)

- Slice 001 entregou o scaffold (`config/`, toolchain, smoke tests) — o gate `ruff+mypy+pytest` está verde.
- Referência somente-leitura: `/projects/dev/ats-web/config/settings/db.py` (padrão de resolução `DATABASE_URL` → `DB_*` → `DB_PASSWORD_FILE` — atenção: a versão do ats-web retorna `{}` silenciosamente quando falta senha; o HMD **não copia esse fallback**, ver R1), `docker-compose.yml` (Postgres + init em `docker/postgres/init.sql` com `unaccent`/`pg_trgm` + healthcheck), `docker-compose.dev.yml`, `docker-compose.test.yml` (tmpfs, porta 5433), `.env.example`.
- Design: `../design.md` D3 e D7 (decisões de settings e de testes com PostgreSQL).
- Spec: `../specs/project-infrastructure/spec.md` (cenários de precedência, compose dev, settings de teste).

## Requisitos

- **R1** `config/settings/db.py` expõe uma função pura de resolução (ex.: `database_config(env) -> dict`) com: `DATABASE_URL` presente → configuração derivada dela; ausente → montagem por `DB_*`; `DB_PASSWORD_FILE` lê o conteúdo do arquivo como senha; configuração insuficiente (ex.: sem URL e sem senha) → **exceção explícita** (`ImproperlyConfigured`) — divergência deliberada do ats-web, que retorna `{}` silenciosamente nesses casos; o HMD falha fechado.
- **R1b** A mesma função pura resolve o **banco de teste** quando chamada com prefixo `TEST_` (`TEST_DATABASE_URL` → `TEST_DB_*`), e as settings `test.py` usam essa resolução — nunca `DATABASE_URL`/`DB_*` de desenvolvimento.
- **R2** `docker-compose.yml` sobe PostgreSQL 17 com healthcheck e `docker/init.sql` habilitando `unaccent` e `pg_trgm` no banco da aplicação.
- **R3** `docker-compose.dev.yml` sobe o serviço `web` (runserver) usando a imagem/comando compatíveis com `uv`; banco aponta para o serviço db.
- **R4** `docker-compose.test.yml` sobe Postgres efêmero (tmpfs, porta host 5433, banco `hmd_test`), sem volume persistente.
- **R5** `uv run pytest` roda com settings de teste apontando para o banco de teste (porta/nome distintos do dev); um teste de migração (criar banco de teste e aplicar migrações) passa; um teste afirma que o banco das settings de teste **difiere** do banco das settings de dev mesmo com `DATABASE_URL` de dev definido no ambiente (proteção contra a suíte tocar o banco dev).
- **R6** `.env.example` documenta todas as vars novas (`DATABASE_URL`, `DB_*`, `DB_PASSWORD_FILE`, `TEST_DATABASE_URL`, `TEST_DB_*`, `TEST_DB_PORT`).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1/R1b | `config/settings/db.py`, `config/settings/test.py`, `tests/test_db_settings.py` | `uv run pytest tests/test_db_settings.py` |
| R2 | `docker-compose.yml`, `docker/init.sql` | `docker compose up -d db` + healthcheck `pg_isready`; `psql -c "select 1 from unaccent('á')"` opcional |
| R3 | `docker-compose.dev.yml` | `docker compose -f docker-compose.yml -f docker-compose.dev.yml up` e `curl` na porta dev |
| R4 | `docker-compose.test.yml` | `docker compose -f docker-compose.test.yml up -d` + `pg_isready -p 5433` |
| R5 | `config/settings/test.py`, `conftest.py` | `uv run pytest` (inclui teste de migração + teste de isolamento dev↔teste) |
| R6 | `.env.example` | `rg -n "DB_PASSWORD_FILE|TEST_DATABASE_URL|TEST_DB_PORT" .env.example` |

## RED

- Comando: `uv run pytest tests/test_db_settings.py`
- Falha esperada: `ImportError`/`AttributeError` — função de resolução não existe no `db.py` (stub do slice 001).

## GREEN / verificação local

- `uv run pytest tests/test_db_settings.py` — exit 0
- `uv run pytest` — exit 0 (com compose de teste no ar)
- `docker compose up -d db && uv run python manage.py migrate --settings=config.settings.dev` — migrações aplicadas
- `uv run ruff check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - config/settings/db.py
  - config/settings/{base,test,dev}.py   # ajustes pontuais de integração
  - docker-compose.yml
  - docker-compose.dev.yml
  - docker-compose.test.yml
  - docker/init.sql
  - tests/test_db_settings.py
  - .env.example
allowed_incidental_files:
  - Dockerfile (mínimo p/ compose dev, se necessário ao serviço web)
out_of_scope:
  - apps/ de domínio e migrations de app (slice 003)
  - workers django-q2 (change futuro)
```

Escale ao parent se: o compose dev exigir imagem de aplicação com dependências além do trivial; o serviço `web` não subir sem passos manuais não documentados; precisar mudar decisões de D3/D7.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz
- [ ] `migrate` aplica no compose dev sem intervenção manual
- [ ] Suíte roda contra banco de teste isolado (nenhum teste toca o banco dev)
- [ ] Gate parcial do slice verde
