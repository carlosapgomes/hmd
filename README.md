# HMD — Hemodinâmica

Sistema de apoio à regulação de pacientes do serviço de hemodinâmica do HGRS.
Clone de padrões do `ats-web` (monolito Django SSR), com autenticação AD/Kerberos
em estágio posterior, anonimização Presidio fail-closed e catálogo de 13 tipos
de procedimento.

## Documentação

- `AGENTS.md` — regras, stack, comandos, política de testes, workflow OpenSpec.
- `PROJECT_CONTEXT.md` — contexto executivo, fontes autoritativas e roadmap
  dos 11 changes.
- `docs/adr/` — decisões arquiteturais (ADR-0001 a 0004).

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

## Autenticação AD (Kerberos) e break-glass

Usuários provisionados com `ad_upn` (`cpf@dominio`, via Django admin)
autenticam exclusivamente via Active Directory (Kerberos AS-REQ com a senha
digitada). O realm é derivado do sufixo do UPN no momento da validação — não é
configurável. DCs e timeout vêm do ambiente (`AD_DCS`, `AD_KDC_TIMEOUT`).

A autenticação local é apenas **break-glass**: superusuário **sem** `ad_upn`,
quando `AD_ALLOW_LOCAL_AUTH=true` (default **false** em produção; dev/test
setam `true` explicitamente). A validação contra os DCs reais é manual e fora
do CI (ver `ad_check` abaixo). Detalhes em `docs/adr/ADR-0004-*.md`.

### `ad_check` — aceitação contra os DCs reais (manual, fora do CI)

Valida uma senha AD contra **cada** DC configurado e imprime por DC o
resultado (ok/código/razão/latência). A senha é lida via prompt (`getpass`) —
nunca em argumentos, variável de ambiente ou logs:

```bash
uv run python manage.py ad_check --cpf 12345678901@dominio-teste.local
```

Requer rede hospitalar; útil para a matriz de aceitação da pesquisa (senha
errada `24`, CPF inexistente `6`, DC `.19` fora → `.21`, etc.).

## Testes

A suíte usa settings próprias (`config.settings.test`) com banco de teste
dedicado (porta 5433), isolado do banco de desenvolvimento:

```bash
uv run pytest
```

## Benchmark de anonimização (aceite operacional)

O harness `anonymization_benchmark` avalia um corpus de textos em JSONL (uma
entrada por linha `{"text": ..., "expected": [{"value": ...,
"entity_type": ...}]}`) contra o núcleo de anonimização: recall por tipo de
entidade, contagens, latência p50/p95 por documento, varredura zero-PII no
output (CPF/CNS com checksum), pico de RSS do processo e documentos
bloqueados. O comando falha (exit ≠ 0) quando algum tipo fica abaixo do
recall mínimo, um vestígio de PII é encontrado, um documento bloqueia ou o
RSS excede o limite opcional:

```bash
# Corpus sintético versionado (roda na suíte; exit 0 esperado)
uv run python manage.py anonymization_benchmark \
  --corpus apps/anonymization/tests/fixtures/benchmark_corpus.jsonl \
  --settings=config.settings.dev

# Corpus real do serviço (pré-produção) — ajuste o mínimo por env
ANONYMIZATION_BENCHMARK_MIN_RECALL=0.90 \
  uv run python manage.py anonymization_benchmark --corpus corpora/reais.jsonl

# Limite duro opcional de RSS (MB; sem limite por default)
ANONYMIZATION_BENCHMARK_MAX_RSS_MB=900 \
  uv run python manage.py anonymization_benchmark --corpus corpora/reais.jsonl
```

O recall mínimo default é o setting `ANONYMIZATION_BENCHMARK_MIN_RECALL`
(0.90), sobreponível por `--min-recall`. Corpus sintético versionado acompanha
a suíte; a aceitação com relatórios reais é passo operacional manual,
pré-produção (ADR-0007).
