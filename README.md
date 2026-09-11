# HMD — Hemodinâmica

Sistema de apoio à regulação de pacientes do serviço de hemodinâmica do HGRS.
Clone de padrões do `ats-web` (monolito Django SSR), com autenticação AD/Kerberos
em estágio posterior, anonimização Presidio fail-closed e catálogo de 13 tipos
de procedimento.

## Documentação

- `AGENTS.md` — regras, stack, comandos, política de testes, workflow OpenSpec.
- `PROJECT_CONTEXT.md` — contexto executivo, fontes autoritativas e roadmap
  dos 11 changes.
- `docs/adr/` — decisões arquiteturais (ADR-0001 a 0009).

## Stack

Python 3.13+ · Django 5.2+ · PostgreSQL 17+ · Bootstrap 5.3 · Vanilla JS · uv

## Status

**v0.1.0 — roadmap completo (11 changes)**. Ciclo do caso de ponta a ponta
(`NEW→CLEANED`): upload com anexos, extração/anonimização fail-closed,
pipeline LLM por tipo (só tokens), decisão médica consultiva, agendamento
em 2 unidades com intercorrência, resposta final/ciência com limpeza,
reenvio corrigido, notificações in-app, painel gerencial, PWA e manual.
1037 testes · 13 specs promovidas (`openspec/`). Veja `CHANGELOG.md`.

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

## Autenticação AD (Kerberos) e admin local por design

Usuários provisionados com `ad_upn` (`cpf@dominio`, via Django admin)
autenticam exclusivamente via Active Directory (Kerberos AS-REQ com a senha
digitada). O realm é derivado do sufixo do UPN no momento da validação — não é
configurável. DCs e timeout vêm do ambiente (`AD_DCS`, `AD_KDC_TIMEOUT`).

O superusuário **sem** `ad_upn` autentica localmente em qualquer ambiente, por
design (ADR-0009): a credencial do AD é assistencial única no hospital e o
admin do sistema é uma **identidade local permanente** — não existe flag de
habilitação; preencher `ad_upn` no superusuário o torna autenticável por AD. O
login do Django admin é coberto pelo mesmo anti-lockout local dos demais
logins. Detalhes em `docs/adr/ADR-0004-*.md` e `docs/adr/ADR-0009-*.md`.

### `ad_check` — aceitação contra os DCs reais (manual, fora do CI)

Valida uma senha AD contra **cada** DC configurado e imprime por DC o
resultado (ok/código/razão/latência). A senha é lida via prompt (`getpass`) —
nunca em argumentos, variável de ambiente ou logs:

```bash
uv run python manage.py ad_check --cpf 12345678901@<dominio-ad>
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

## Primeiros testes (implantação interna)

Release `v0.1.0` destina-se a testes internos na intranet do HGRS. O caminho
mais rápido é o compose de desenvolvimento em um servidor interno (runserver +
workers), enquanto o serviço web de produção (gunicorn) não é adicionado.

```bash
# 1. Código + imagem (workers/pdf/anonymization/llm/attachments usam a
#    imagem pronta com o modelo spaCy pt_core_news_lg no build):
git clone https://github.com/carlosapgomes/hmd.git && cd hmd
docker compose -f docker-compose.yml -f docker-compose.dev.yml build

# 2. Ambiente (.env na raiz — modelo em .env.example):
#    - DJANGO_SECRET_KEY (obrigatório; gere um segredo real)
#    - DATABASE_URL / credenciais do Postgres
#    - OPENROUTER_API_KEY + LLM_MODEL_LLM1/LLM2 (pipeline; sem elas os
#      casos ficam retidos fail-closed)
#    - VISION_MODEL (OCR externo de anexos; sem ela anexos de imagem
#      falham nomeados — extração local de PDFs segue)
#    - ALLOWED_HOSTS com o nome do servidor de teste

# 3. Banco + dados-base (idempotentes — re-executar é seguro):
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm web \
  python manage.py migrate --settings=config.settings.dev
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm web \
  python manage.py seed_admin --settings=config.settings.dev   # papéis + admin local
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm web \
  python manage.py seed_prompts --settings=config.settings.dev  # 29 prompts versionados

# 4. Sobe tudo (web + worker-pdf/anonymization/llm/attachments):
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Checklist pós-install: `manage.py ad_check` (se AD/Kerberos estiver no ar),
criar usuários de teste com os papéis (admin local → perfil), atribuir
especialidades aos médicos, e conferir o sino de notificações/painel/manual
na navbar. Os 13 tipos de procedimento vêm do catálogo; especialidades médicas
vêm da migration `0003` (idempotente).

Limitações conhecidas deste release: workers exigem `VISION_MODEL` para OCR
externo; benchmark com corpus real e revisão dos prompts são aceite
pré-produção (`CHANGELOG.md` → pendências).
