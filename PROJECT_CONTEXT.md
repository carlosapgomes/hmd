# PROJECT_CONTEXT.md

## Propósito

Resumo executivo para retomada rápida após pausas e onboarding de
implementadores com contexto zero.

## Fontes autoritativas

- `AGENTS.md` — regras, stack, comandos, política de testes, workflow.
- `README.md` — instruções de ambiente e operação local.
- `openspec/changes/` — changes ativos (proposals, designs, specs, slices).
- `docs/adr/` — decisões arquiteturais (ADR-0001 a 0003 no bootstrap).
- Roadmap e parâmetros de negócio: `temp/plano-implementacao-hmd.md` e
  `temp/parametrosHMD.md` (fonte para os 13 tipos de procedimento e thresholds
  clínicos — consumidos a partir do change 03).
- Projeto-fonte de padrões (somente-leitura): `/projects/dev/ats-web`.
- Em caso de conflito, artefatos mais recentes no Git prevalecem; decisões
  registradas em ADR prevalecem sobre texto de slice.

## Objetivo do sistema

Sistema de **apoio à regulação de pacientes do serviço de hemodinâmica** do
HGRS. O NIR envia relatórios de regulação (PDF) com a declaração dos
procedimentos; o sistema extrai, anonimiza (Presidio, obrigatório antes de
qualquer LLM), estrutura via LLM e apresenta ao médico uma recomendação
**consultiva** (nunca bloqueia); após a decisão médica, o agendador confirma
data/hora/local (unidade 1 ou 2) e o resultado retorna ao NIR.

Diferenças fundamentais vs. o projeto-fonte `ats-web` (EDA): autenticação via
Active Directory (Kerberos) em estágio posterior; anonimização Presidio
fail-closed; catálogo de 13 tipos de procedimento com schemas/prompts por tipo;
policy clínica consultiva determinística; agendamento multi-unidade;
django-fsm substituído por viewflow.fsm com estados renomeados.

## Estado atual (execução do roadmap)

Change 01 `bootstrap-django-hmd-core` **implementado** (6/6 slices aceitos; gate final verde: ruff + format + mypy + 61 testes + `manage.py check`; aguardando arquivamento). Fundação completa: scaffold Django 5.2 SSR (`config/settings` por ambiente com resolução de banco fail-closed `DATABASE_URL`→`DB_*`→`DB_PASSWORD_FILE` e prefixo `TEST_` isolado), compose dev/test PostgreSQL 17 (+unaccent/pg_trgm), `apps/accounts` (User multi-role com `account_status`/conselho, seed_admin idempotente, auth local transitória com backend que recusa conta não-ativa, papel ativo em sessão revalidado a cada request, switch-role, `role_required` 403, `IntranetGuardMiddleware` nir-only por papel ativo). Próximo: change 02 `ad-kerberos-authentication`.

Nota ambiental: portas host 5432/5433 podem estar ocupadas por containers de outros projetos — use `POSTGRES_HOST_PORT=55432`/`TEST_DB_PORT=55433` (mecanismo já previsto nos compose/env).

## Roadmap — 11 changes (resumo)

| # | Change | Conteúdo vertical |
|---|---|---|
| 01 | `bootstrap-django-hmd-core` | Scaffold, settings, docker-compose, accounts (papéis/multi-role/guard nir-only), templates base, AGENTS/PROJECT_CONTEXT, ADRs 0001–0003, seed_admin |
| 02 | `ad-kerberos-authentication` | `ad_upn`, KerberosBackend (minikerberos), failover, códigos, rate-limit, login CPF+senha, ADR |
| 03 | `case-core-fsm-procedures` | Case/CaseProcedure/CaseEvent/locks/comunicações, FSM viewflow (17 estados), catálogo 13 tipos + exam profiles |
| 04 | `intake-nir-upload` | Upload multi-PDF, worker pdf (PyMuPDF), regulation gate adaptado, declaração de tipos, meus casos |
| 05 | `presidio-anonymization` | App anonymization, pré-extração determinística, pseudônimos, worker, fail-closed, recognizers BR, ADR |
| 06 | `llm-pipeline-per-type` | Cliente OpenRouter, schemas por tipo + composição união, LLM1/LLM2, reconciliação, policy consultiva, prior-case, prompts versionados, ADR |
| 07 | `doctor-queue-decision` | Fila + filtro por subtipo, presenter re-identificado, alertas da policy, decisão por procedimento |
| 08 | `scheduler-multi-unit` | Fila, confirmar/desmarcar com unidade 1\|2, resposta final unidade 2 ao NIR, intercorrência (unidade 1) |
| 09 | `nir-result-closure` | Resultado final, confirmar recebimento → CLEANED, casos encerrados, reenvio corrigido |
| 10 | `attachment-processing-ocr` | OCR híbrido (local/visão), anonimização de anexo, verificação patient-match |
| 11 | `dashboard-notifications-pwa` | Dashboard por tipo/unidade, notificações in-app, PWA (ícone HMD), manual de usuário |

## Estrutura de alto nível (atual)

```
config/            settings por ambiente (base/dev/prod/test), urls, wsgi/asgi
templates/         templates raiz + accounts (login/perfil/home/switch-role)
static/            CSS/JS (tema hospitalar HMD, Bootstrap 5.3 CDN)
tests/             suíte raiz (smoke + resolução de banco)
apps/accounts/     User/Role, backends, views, middleware (papel ativo + guard), seed
docker-compose*.yml PostgreSQL 17 dev/test (+ docker/init.sql unaccent/pg_trgm)
docs/adr/          decisões arquiteturais
```

## Regras não negociáveis

- Monolito Django SSR; sem DRF/SPA; sem e-mails transacionais (ADR-0003).
- Guard de intranet apenas para o papel ativo `nir`, decidido por papel ativo
  em sessão (ADR-0002).
- Policy clínica sempre consultiva — recomenda + explica, nunca bloqueia.
- Anonimização Presidio **antes** de qualquer chamada LLM (fail-closed).
- Settings por ambiente; segredos por env; `prod` falha fechado.
- Quality gate local (ruff + mypy + pytest) é o contrato de verificação.

## Quality bar

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

Projeto de referência (padrões, somente-leitura): `/projects/dev/ats-web`.
