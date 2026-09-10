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

Changes 01–10 arquivados (11 archives; 10 specs promovidas). Change 11 `dashboard-notifications-pwa` **executado** (2026-09-09; 5 slices; gate 1037 testes ×2 rodadas + ruff/format/mypy + `openspec validate --strict` PASS; aguardando autorização para arquivar). **Visibilidade de ponta a ponta (roadmap completo)**: `UserNotification` em apps/accounts (migration 0004) + signal `CaseEvent.post_save` com conjunto fechado de marcos (resposta final→criador com preview por payload.source; pronto-p/agendamento→fan-out schedurers ATIVOS distinct; reabertura=`AWAITING_SCHEDULING` com payload.reason→criador; idempotente por unique(recipient,event)+get_or_create; savepoint no signal — DatabaseError da notificação nunca reverte a transição em silêncio; zero PHI — motivo da reabertura nunca vaza); UI: sino+badge via context processor em toda página, lista com janela de retenção 48h (NOTIFICATION_READ_RETENTION_HOURS; nada apagado), abrir→marca lida+redirect por papel ativo (rotas fazem guard real; alheia=404), marcar-todas, endpoint JSON (rotas GLOBAIS sem namespace — accounts é incluída na raiz); app `apps/dashboard` com métricas puras por período (hoje/7d/30d/tudo; dia local) em **fontes imutáveis** (outcome=source do ÚLTIMO evento final SÓ com status pós-final — reaberto em voo=em andamento, sem dupla contagem; DISTINCT ON por -id; tabela por tipo=rows de CaseProcedure doctor_disposition na ordem canônica; unidade por scheduled_unit; tempo médio max(doctor_decided_at)−created_at) — view login-required transversal, template zero-PHI, link Painel; **PWA**: manifest HMD + sw.js conservador (bypass `includes("/pdf/")` E `"/documents/"` — viewer nativo; nunca POST; HTML autenticado nunca em cache) + ícones HMD SVG+10 PNGs por `scripts/generate_pwa_icons.py` (PyMuPDF, --check) + registro em base.html; manual por papel com os 17 estados reais (anti-drift bidirecional). **Anexos de evidência de ponta a ponta**: novo app `apps/attachments` (`CaseAttachment` com `original_filename`, status `pending|processing|processed|failed`, `extraction_method local_pdf|vision`, textos/mapas/verificação; migration 0001) — upload jpg/png/pdf pelo NIR na criação E reenvio corrigido (kwargs aditivos `attachments=()` em `create_case_with_documents`/`create_corrected_resubmission`; validação contagem≤5/tamanho≤10MB/MIME antes de qualquer gravação; arquivo por UUID no callable antes do INSERT + compensação); **extração híbrida** (PDF com texto → PyMuPDF local; foto/scan/PDF-imagem → `VISION_MODEL` externo com rasterização pixmap, teto 10 páginas, `ensure_vision_ready` fail-closed; **evento de auditoria `CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED` antes do envio — só quando o envio é possível**); worker `process_case_attachments` no **cluster `attachments`** (`ATTACHMENTS_RUN_TASKS_INLINE`, default false em prod — fail-closed como os irmãos) com **idempotência por etapa** (retry não re-envia OCR) e no-op em corrida com a ciência; trigger signal `CASE_ANONYMIZATION_COMPLETED` → on_commit (anexos em paralelo ao pipeline LLM); **anonimização com semeadura do namespace do caso** (`anonymize_text(text, seed_map=...)`/`PseudonymOperator(seed_map)` aditivos: paciente do caso mantém seu token em qualquer ordem, paciente diferente ganha token distinto acima do máximo — sem convergência espontânea entre textos, a numeração é por 1ª ocorrência); **verificação LLM só vê tokens** (`ATTACHMENT_VERIFICATION`, 29º seed idempotente; `LLM1_MODEL` + schema strict `patient_match match|mismatch|unknown`/summary/evidence; sem token do paciente → unknown forçado em código; persistência atômica + `CASE_ATTACHMENT_PROCESSED`; falha → failed sem efeito no caso); **cards consultivos no doctor** (badge por patient_match/status, resumo/evidência re-identificados na renderização com o mapa do ANEXO via `reidentify` puro; mismatch = danger + alerta fixo, NUNCA descarta/bloqueia — decisão com mismatch testada OK); **ciência remove anexos** (rows no atomic D2 + arquivos no mesmo on_commit; eventos append-only; conjunto 09 intacto — delta MODIFIED case-closure com 6 cenários).

Notas operacionais: portas host 5432/5433/55433 flutuam entre projetos — use `POSTGRES_HOST_PORT=55432`/`TEST_DB_PORT=55435`. Produção exige NTP + cache compartilhado + workers no ar (flags `*_RUN_TASKS_INLINE=false`); LLM em prod exige `OPENROUTER_API_KEY` + `LLM_MODEL_*` (modelos por env). Diagnósticos: `manage.py ad_check` (AD), `manage.py llm_check` (OpenRouter), `manage.py anonymization_benchmark --corpus <real>` (**aceite pré-produção: relatórios reais e revisão de recall por entidade**), `manage.py seed_prompts` (idempotente). Pendências de validação em ambiente: qcluster real (worker-pdf/worker-anonymization/worker-llm/worker-attachments — precisa VISION_MODEL no ambiente p/ OCR externo) e `docker build` das imagens de worker (~700 MB com modelo); **benchmark de modelos flash + corpus real de anonimização pré-produção**; P2s do 06: `released=True` só após commit do atomic (anonymization/orchestrador), branch async de conflito sem teste direto, revisão humana dos 28 prompts; **K textual-informativo pendente de validação do dono**. P2s do admin-local: replicação do form no pre-check do /admin (sincronizar em upgrades do Django), `_wrapped` privado no teste de wiring, ADR-0004 fica histórico (0009 o emenda). P2s do 07: teste do ramo fail-closed do access, assertNumQueries da fila, card de decisões gateado por decision_event (rows são a fonte), re-render de POST inválido em corrida perdedora. P2s do 08: `scheduling_reopen_reason` persiste pós-reconfirmação e segue no card de agendamento (histórico vive nos eventos), mensagens default EN nos required do form de confirmação, duplicação de helpers de teste entre test_services/test_incident, teste de atomicidade simula corrida via validação concorrente. P2s do 09: casos de negativa total não aparecem mais na aba "decididos" do doctor (vão direto a FINAL_REPLY_POSTED — decisão de produto a confirmar com o dono), 3ª cópia do _CATALOG_ORDER (closure.py), `_delete_files_best_effort` captura só OSError, highlight da resposta final com teste reforçado pelo parent, enqueue do reenvio em cascata no atomic externo (inline=True = desvio conhecido), ordem de validação do reenvio criador-antes-de-estado (superior ao narrado). P2s do 10: mapeamento SDK→LlmError duplicado entre vision.py e OpenRouterClient (drift possível; testes só no pipeline), sem `select_for_update` por anexo (execuções sobrepostas do mesmo caso poderiam duplicar dispatch — retry sequencial coberto), fallback do badge "unknown" p/ processed sem match é inalcançável, upload suplementar pós-criação e supressão auditável de anexo fora de escopo (existem no ats-web). Quirk de harness documentado: flush de TransactionTestCase + --reuse-db apaga seeds de migration (mitigado com teste chamando a função da migration + fixtures get_or_create); após mudar migration rodar `pytest --create-db` uma vez. P2s do 11: ícones "any" com contraste baixo em superfície escura (ats-web tem variante -contrast; decisão de design), drift de regeneração dos PNGs não detectado pelo --check (só presença), cache.addAll tudo-ou-nada no install do SW (asset futuro ausente = SW nunca ativa silenciosamente), fallback offline de navegação inerte por decisão (HTML autenticado nunca em cache), read_at gravado antes do guard do destino (destinos nir/scheduler seguros; doctors não recebem notificações), notificações sem registro no Django admin, dashboard sem cache/export (contagens ao vivo). Próximo: arquivamento do 11 (fim do roadmap de 11 changes — pendências pré-produção no topo desta nota).

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
apps/anonymization/ deterministic, recognizers BR (checksum), engine Presidio singleton, services (núcleo+wrapper), tasks (cluster anonymization, fail-closed; release+transição no mesmo atomic), reidentify (+reidentify_structure recursivo), benchmark
apps/pipeline/      llm.py (cliente OpenRouter+erros), schemas/ (base+ blocos+união), llm1_service, llm2_service, procedure_reconciliation, policy, prior_case, ptbr_language_guard, json_parser, orchestrator+tasks (cluster llm) + signals (trigger duplo)
apps/llm/          PromptTemplate versionado (constraint parcial 1-ativo/nome), prompts_seed (28), services (build_case_prompts), seed_prompts
apps/doctor/       fila por estado+subtipo (Paginator), access.py (matriz por papel ativo), presenters (re-identificado), forms (decisão por procedimento), views (fila/detalhe/decide/PDF)
apps/scheduler/    services (confirmar/negar/intercorrência c/ resposta final ao NIR por unidade), fila (abas, Paginator), presenters (detalhe limitado c/ one-liner re-identificado), forms, views (fila/detalhe/ações/PDF por position)
apps/cases/closure.py  post_doctor_denial_reply (negativa → resposta final) + acknowledge_case_receipt (ciência + limpeza/minimização transacional)
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
