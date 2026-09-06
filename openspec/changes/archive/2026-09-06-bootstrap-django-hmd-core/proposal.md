# Proposal: bootstrap-django-hmd-core

## Why

O HMD (apoio à regulação do serviço de hemodinâmica do HGRS) ainda não existe como projeto: o repositório contém apenas `README.md` e `openspec/` vazio. Todo o roadmap aprovado (`temp/plano-implementacao-hmd.md`, 11 changes) depende de uma base executável: monolito Django SSR com toolchain de qualidade, gestão de papéis de usuário e documentação viva. Este change cria essa fundação, clonando os padrões arquiteturais do projeto fonte `/projects/dev/ats-web` (clone fresh de padrões, não de código — ver `docs/adr/ADR-0001`).

## What Changes

- Scaffold Django 5.2 SSR (`config/settings/{base,dev,prod,test,db}.py`, `urls.py`, `wsgi/asgi`), empacotado com `uv`.
- Toolchain de qualidade: `ruff` (lint+format), `mypy` (django-stubs), `pytest` + `pytest-django`, com quality gate documentado.
- Infra dev/teste: `docker-compose.yml` (PostgreSQL 17 + init `unaccent`/`pg_trgm`), `docker-compose.dev.yml` (web), `docker-compose.test.yml` (db de teste efêmera, porta 5433).
- App `apps/accounts`: `User(AbstractUser)` com `roles` M2M, `account_status`, `professional_council`(+número) e display name derivado; `Role` com os 5 papéis fixos (`nir`, `doctor`, `scheduler`, `manager`, `admin`); comando `seed_admin`.
- Autenticação **local transitória** (ModelBackend + login/logout) — será substituída pela autenticação AD/Kerberos no change seguinte (`ad-kerberos-authentication`); superuser local break-glass permanece (ADR-0003).
- Multi-role com **papel ativo único em sessão**: middleware, view `switch-role`, context processor, decorator `@role_required`.
- **Intranet guard apenas para `nir`** (diferença deliberada vs ats-web, onde `scheduler` também era restrito — scheduler do HMD acessa via internet pela unidade 2).
- Templates base com tema hospitalar, páginas de login, troca de papel e perfil.
- Documentação viva: `AGENTS.md` (regras/stack/comandos/política de testes), `PROJECT_CONTEXT.md`, `README.md` atualizado e ADRs 0001–0003.

## Capabilities

### New Capabilities

- `project-infrastructure`: fundação executável do repositório — settings por ambiente (12-factor), ambiente dev reproduzível via docker compose e quality gate local determinístico.
- `account-access`: gestão de acesso — papéis fixos, usuário multi-role com papel ativo em sessão, proteção de views por papel, restrição de intranet para `nir`, autenticação local transitória e seed administrativo.

### Modified Capabilities

(nenhuma — primeiro change do repositório)

## Impact

- **Código**: cria `config/`, `apps/accounts/`, `templates/`, `static/`, `tests/` (smoke), `docs/adr/`, `conftest.py`, `pyproject.toml`, `docker-compose*.yml`, `.env.example`; atualiza `README.md`; cria `AGENTS.md` e `PROJECT_CONTEXT.md`.
- **Dependências novas**: Django 5.2, psycopg (driver), django-environ (ou parser próprio — decidido em design), ruff, mypy, django-stubs, pytest, pytest-django, whitenoise. **Sem** django-fsm (changes posteriores usarão viewflow.fsm), **sem** django-q2 (entra com o primeiro worker, change 04), **sem** e-mails transacionais (fora de escopo permanente — ADR-0003).
- **Sistemas externos**: nenhum (AD/Kerberos, Presidio e OpenRouter chegam em changes posteriores).
- **Referência de padrões**: `/projects/dev/ats-web` é fonte **somente-leitura** de padrões (middleware, decorators, modelos, templates); nenhum arquivo é copiado integralmente sem adaptação ao contexto HMD.
