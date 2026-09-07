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
django-fsm substituído por django-fsm-2 (MIT; API django-fsm preservada — viewflow.fsm descartado por AGPLv3+) com estados renomeados.

## Estado atual (execução do roadmap)

Change 01 `bootstrap-django-hmd-core` arquivado (2026-09-06). Change 02 `ad-kerberos-authentication` arquivado (2026-09-07). Change 03 `case-core-fsm-procedures` **arquivado** (2026-09-07; capability `case-management` promovida — 6 requirements/18 cenários). Núcleo de domínio em `apps/cases`: catálogo code-first dos 13 procedimentos (`procedure_catalog.py` com thresholds S1–S8 e suporte anestésico validado), `Case` com FSM django-fsm-2 (17 estados, 18 transições protegidas, tabela no ADR-0005/design D4), `CaseEvent` append-only (gravação atômica direta, `actor_role` explícito, tipos canônicos em `events.py`), `CaseProcedure` neutro + serviços atômicos (declaração/detecção/decisão com encadeamento transacional da aceitação), locks/lease com `select_for_update` (`CaseLockConflictError`), comunicações user/system projetadas de eventos. **Guardrails**: states do FSM e catálogo são contrato — changes futuros adicionam transições, não estados; procedimentos vivem só em `CaseProcedure`. Próximo: change 04 `intake-nir-upload`.

Notas operacionais: portas host 5432/5433 (e eventualmente 55433) podem estar ocupadas por outros projetos — use `POSTGRES_HOST_PORT=55432`/`TEST_DB_PORT=55435` (mecanismo previsto nos compose/env). Produção exige NTP (código 37) e cache compartilhado (`prod` aborta com LocMem). Diagnóstico AD: `manage.py ad_check --cpf cpf@dominio` (getpass; fora do CI).

## Roadmap — 11 changes (resumo)

| # | Change | Conteúdo vertical |
|---|---|---|
| 01 | `bootstrap-django-hmd-core` | Scaffold, settings, docker-compose, accounts (papéis/multi-role/guard nir-only), templates base, AGENTS/PROJECT_CONTEXT, ADRs 0001–0003, seed_admin |
| 02 | `ad-kerberos-authentication` | `ad_upn`, KerberosBackend (minikerberos), failover, códigos, rate-limit, login CPF+senha, ADR |
| 03 | `case-core-fsm-procedures` | Case/CaseProcedure/CaseEvent/locks/comunicações, FSM django-fsm-2 (17 estados), catálogo 13 tipos + exam profiles |
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
apps/accounts/     User/Role (ad_upn), backends (Kerberos AD + break-glass), kerberos.py (cliente/failover), ratelimit.py (anti-lockout), middleware (papel ativo + guard), admin, ad_check
apps/cases/        procedure_catalog (13 tipos/S1–S8), Case+FSM (django-fsm-2, 17 estados), CaseEvent/CaseProcedure, procedures.py, locks.py, communications.py+signals
docker-compose*.yml PostgreSQL 17 dev/test (+ docker/init.sql unaccent/pg_trgm)
docs/adr/          decisões arquiteturais (0001–0005)
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
