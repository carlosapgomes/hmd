# HMD — Hemodinâmica

Sistema de apoio à regulação de pacientes do serviço de hemodinâmica do HGRS.
Clone de padrões do `ats-web` (monolito Django SSR), com autenticação AD/Kerberos
em estágio posterior, anonimização Presidio fail-closed e catálogo de 13 tipos
de procedimento.

## Documentação

- `AGENTS.md` — regras, stack, comandos, política de testes, workflow OpenSpec.
- `PROJECT_CONTEXT.md` — contexto executivo, fontes autoritativas e roadmap
  dos 11 changes.
- `docs/adr/` — decisões arquiteturais (ADR-0001 a 0003).

## Stack

Python 3.13+ · Django 5.2+ · PostgreSQL 17+ · Bootstrap 5.3 · Vanilla JS · uv

## Status

Bootstrap em andamento (change 01). O slice 001 entrega o scaffold executável
(settings por ambiente, toolchain de qualidade e smoke tests) **sem banco de
dados**; o ambiente docker compose e as migrações chegam no slice 002.

## Ambiente de desenvolvimento

Pré-requisitos: Python 3.13, `uv`.

```bash
# 1. Instalar dependências (cria .venv + uv.lock se necessário)
uv sync

# 2. Servidor de desenvolvimento
uv run python manage.py runserver --settings=config.settings.dev
#    Home estática mínima em http://127.0.0.1:8000/

# 3. Qualidade
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

Variáveis de ambiente documentadas em `.env.example` (`cp .env.example .env`).
Settings por ambiente em `config.settings.{base,dev,prod,test}`; `prod` falha
fechado sem `DJANGO_SECRET_KEY`.

## Testes

A suíte usa settings próprias (`config.settings.test`) com banco de teste
dedicado (porta 5433), isolado do banco de desenvolvimento:

```bash
uv run pytest
```
