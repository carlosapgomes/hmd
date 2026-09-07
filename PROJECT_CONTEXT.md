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

Changes 01–05 arquivados. Change 06 `llm-pipeline-per-type` **executado** (2026-09-08; 6 slices; gate 651 testes + ruff/mypy + `openspec validate --strict` PASS). Pipeline LLM completo em `apps/pipeline` + `apps/llm`: cliente OpenRouter (SDK OpenAI 3.8.0; URL `https://openrouter.ai/api/v1`; factory injetável `LLM_CLIENT_FACTORY` — suíte sem rede), `llm_check` (diagnóstico de chave), schemas Pydantic v2 strict por tipo (base comum com `evidence_spans` obrigatória; blocos específicos dos tipos c/ artefatos; `pedido.procedimentos_solicitados` Literal **gerado do catálogo**; normalização `oneOf→anyOf` p/ response_format), `apps/llm` PromptTemplate versionado (constraint parcial 1-ativo-por-nome no banco; 28 seeds idempotentes = 2 system neutros + 13×2 user; montagem só com placeholders — **substituição via `str.replace`, nunca `.format`**), LLM1 extração estruturada (guardas: strict parse + language guard com marcadores versionados + retry único sem reenvio; assert de tokens vs `pseudonym_map` antes de persistir; falha → `LlmPipelineError` tipado), reconciliação + **gate de divergência** (declaração×detecção com upsert `record_detected_procedures`; divergência retém em `LLM_EXTRACTING` com `manual_review_required`; primeira transição nova pós-change-03: `bypass_pipeline_divergence` → `LLM_SUMMARIZING`; despacho do `release_retained_case` por (estado, razão)), policy clínica **consultiva determinística** (tabela de critérios S1–S8: seção fora do threshold = recusa; gerais = informativos; `nao_informado` nunca recusa; anticoagulantes por fármaco — Pradaxa 7d/Xarelto 3d/Eliquis·Lixiana 48h/heparina por dose; **K sem faixa no documento = textual-informativo por decisão do parent 2026-09-07, pendente de validação do dono**), prior-case (nº atendimento 7d > fallback nome normalizado+nascimento 15d; intervalo FECHADO; motivo ANONIMIZADO), LLM2 sumário+sugestão (visão filtrada efêmera; assert de tokens RECURSIVO contra mapas do caso E prévios; **policy prevalece: recusa policy ⇒ recusa**; agregado = qualquer sugestão final recusar ⇒ recusa com motivos somados), orquestrador `process_case_pipeline` fail-closed (dono ÚNICO do `fail_processing`; trigger duplo anti-recursão: entrada `source==ANONYMIZING`; retomada `source==LLM_EXTRACTING` E `actor_type==user`; avanço natural ator system não re-enfileira; retomada pós-bypass **não re-executa LLM1**; cluster `llm` workers 1 + `worker-llm` no compose), ADR-0008. Invariantes: **LLM só vê `anonymized_text`/tokens**; médico vê dados reais (re-identificação).

Notas operacionais: portas host 5432/5433/55433 flutuam entre projetos — use `POSTGRES_HOST_PORT=55432`/`TEST_DB_PORT=55435`. Produção exige NTP + cache compartilhado + workers no ar (flags `*_RUN_TASKS_INLINE=false`); LLM em prod exige `OPENROUTER_API_KEY` + `LLM_MODEL_*` (modelos por env). Diagnósticos: `manage.py ad_check` (AD), `manage.py llm_check` (OpenRouter), `manage.py anonymization_benchmark --corpus <real>` (**aceite pré-produção: relatórios reais e revisão de recall por entidade**), `manage.py seed_prompts` (idempotente). Pendências de validação em ambiente: qcluster real (worker-pdf/worker-anonymization/worker-llm) e `docker build` das imagens de worker (~700 MB com modelo); **benchmark de modelos flash + corpus real de anonimização pré-produção**; P2s registrados: `released=True` só após commit do atomic (anonymization/orchestrador — padrão do intake é mais robusto), branch async de conflito de lock sem teste direto, revisão humana dos textos dos 28 prompts; **K textual-informativo pendente de validação do dono**. Próximo: change 07 `doctor-queue-decision`.

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
apps/cases/        procedure_catalog (13 tipos/S1–S8), Case+FSM (django-fsm-2, 17 estados, +bypass_pipeline_divergence), CaseEvent/CaseProcedure/CaseDocument, procedures.py (declaração+upsert detecção), locks.py, communications.py+signals
apps/intake/       upload/declaração, pdf_utils (PyMuPDF), regulation_gate (SESAB), tasks (cluster pdf), meus casos/detalhe/gate
apps/anonymization/ deterministic, recognizers BR (checksum), engine Presidio singleton, services (núcleo+wrapper), tasks (cluster anonymization, fail-closed; release+transição no mesmo atomic), reidentify, benchmark
apps/pipeline/      llm.py (cliente OpenRouter+erros), schemas/ (base+ blocos+união), llm1_service, llm2_service, procedure_reconciliation, policy, prior_case, ptbr_language_guard, json_parser, orchestrator+tasks (cluster llm) + signals (trigger duplo)
apps/llm/          PromptTemplate versionado (constraint parcial 1-ativo/nome), prompts_seed (28), services (build_case_prompts), seed_prompts
docker-compose*.yml PostgreSQL 17 dev/test (+ docker/init.sql unaccent/pg_trgm)
docs/adr/          decisões arquiteturais (0001–0008)
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
