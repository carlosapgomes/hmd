# AGENTS.md

## 1. Stack e versões

- **Backend:** Python 3.13+, Django 5.2 LTS (SSR — sem API REST/SPA).
- **Banco:** PostgreSQL 17 (psycopg 3). Detalhes de compose/migrações no slice 002.
- **Frontend:** Templates Django, Bootstrap 5.3 (via CDN), CSS customizado em
  `static/css/`, JS vanilla — sem framework JS.
- **Autenticação:** local transitória no bootstrap; AD/Kerberos em change
  posterior (ADR-0003). Sem e-mails transacionais — fora de escopo permanente.
- **Qualidade:** `uv` (lock versionado) · `ruff` (lint+format, linha 100,
  alvo py313) · `mypy` (strict + django-stubs) · `pytest` + `pytest-django`.

Projeto-fonte de padrões (somente-leitura): `/projects/dev/ats-web`. O HMD
clona **padrões** (estrutura/convenções), nunca copia código às cegas.

## 2. Comandos de validação (Quality Gate)

- Lint: `uv run ruff check .`
- Formato: `uv run ruff format --check .`
- Tipos: `uv run mypy .`
- Testes: `uv run pytest`
- Check do Django (dev): `uv run python manage.py check --settings=config.settings.dev`
- Gate completo: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`

## 3. Comandos essenciais (operação local)

```bash
# Instalar dependências (cria .venv + uv.lock se necessário)
uv sync

# Servidor de desenvolvimento
uv run python manage.py runserver --settings=config.settings.dev

# Rodar a suíte (usa config.settings.test — banco de teste dedicado, porta 5433)
uv run pytest

# Rodar um teste específico
uv run pytest tests/test_smoke.py
```

Ambiente: settings por ambiente em `config.settings.{base,dev,prod,test}`.
`prod.py` falha fechado sem `DJANGO_SECRET_KEY`. Variáveis documentadas em
`.env.example`.

## 4. Arquitetura e constraints

- **Monolito Django SSR** com templates; sem REST API, sem SPA, sem DRF.
- Boundaries explícitos por app Django (`apps/`) com dependências
  unidirecionais; app autocontido (models/views/templates/static/tests).
- Lógica de negócio em `services.py` ou `models.py` — nunca em views/templates.
- Estrutura: `config/` (settings/urls/wsgi/asgi), `apps/` (domínio, a partir do
  slice 003), `templates/`, `static/`, `tests/` (suíte raiz).
- Settings por ambiente; segredos/configuração por env (12-factor), defaults
  seguros só em dev; banco de teste isolado nunca toca o banco de dev.
- Multi-role com papel ativo único em sessão; guard de intranet apenas para o
  papel `nir` (ADR-0002).

## 5. Política de testes

- **TDD obrigatório:** RED (teste falha) → GREEN (mínimo para passar) →
  REFACTOR (limpeza sem quebrar).
- Não iniciar implementação sem o teste do comportamento-alvo falhando.
- Priorizar testes unitários; integração para contratos e fluxos.
- Testes nascem **tipados** (mypy strict roda sobre `config/` e `tests/`).
- A suíte de testes roda com settings próprias (`config.settings.test`) e não
  depende do banco de desenvolvimento.

## 6. Workflow OpenSpec e slices

- Cada change segue: propose → design (obrigatório antes de codar) → slices
  verticais TDD → apply → archive.
- Implementar **um slice vertical por vez**, ponta a ponta, dentro do
  `expected_files` do slice; registrar desvios antes de codar.
- Slices enxutos: tocar poucos arquivos, só o necessário para entregar valor.
- O slice é o contrato: seguir requisitos, matriz de validação e critérios de
  aceitação. Escalar ao supervisor se surgir decisão não prevista no design.
- Manter `PROJECT_CONTEXT.md` atualizado ao arquivar changes.

## 7. Política de commit

- Mensagens rastreáveis e atômicas por slice, em português (ex.:
  `feat(bootstrap): scaffold Django + toolchain (slice 001)`).
- Nunca fazer commit de artefatos temporários (`temp/`), `.env`, caches ou
  relatórios de slice.
- Não commitar fora do escopo do slice ativo.

## 8. Anti-patterns proibidos

- Copiar código do ats-web sem adaptar ao contexto HMD.
- Ampliar escopo do slice (novos apps, dependências ou decisões de produto)
  sem passar pelo design/approval.
- Deixar TODO/FIXME sem issue ou plano.
- Acoplar regras de negócio à camada de apresentação.
- Ignorar o quality gate antes de reportar um slice como concluído.

## 9. Prompt de reentrada

> Leia `AGENTS.md` e `PROJECT_CONTEXT.md` primeiro.
> Implemente APENAS o slice indicado, seguindo o TDD (RED → GREEN → REFACTOR).
> Respeite o blast radius do slice; rode o quality gate da seção 2 antes de
> reportar; atualize `PROJECT_CONTEXT.md` quando o change for arquivado.
