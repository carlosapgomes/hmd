# Design: case-core-fsm-procedures

## Contexto

Generalização do núcleo comprovado do `ats-web` (recon: `temp/research/ats-web-recon.md` seções 3–4 e 8) para 13 tipos de procedimento. Fontes clínicas: `temp/plano-implementacao-hmd.md` §2 (catálogo/thresholds) e §4 (FSM 17 estados); `temp/parametrosHMD.md` (documento original). Pesquisa de FSM: `temp/research/viewflow-fsm.md`.

### D1 — Biblioteca de FSM: **`django-fsm-2`** (decisão pendente de confirmação do dono)

O plano original dizia "viewflow.fsm". A pesquisa (`temp/research/viewflow-fsm.md`) revelou: (a) o pacote é `django-viewflow==2.4.0`, ativo e compatível com Django 5.2/Py3.13, porém **AGPLv3+** — exige validação institucional de licença; (b) sua API (flow class separada + hooks `on_success`, sem signals Django) **diverge do padrão do ats-web**, invalidando parcialmente as referências de código que o HMD clona; (c) **`django-fsm-2==4.2.4`** (django-commons) é **MIT**, drop-in da API django-fsm (`@transition`/`FSMField(protected=True)`/signals `pre_transition`/`post_transition`/`ConcurrentTransitionMixin`), Django 5.2/Py3.13 — mantém as referências do ats-web válidas ~1:1. **Recomendação: django-fsm-2**, pendente de OK do dono (licença MIT não exige validação; AGPL exigiria). Se o dono preferir viewflow apesar da AGPL, apenas este design e o slice 002 mudam (adaptação de API: flow class + hooks em vez de decoradores/signals); estados/transições/specs permanecem.

### D2 — Case neutro (padrão ADR-0004 do ats-web)

A dimensão de procedimento vive **exclusivamente** em `CaseProcedure` (1–N rows por caso; `UniqueConstraint(case, procedure_type)`; sem `Case.exam_type` — lição da migration `cases.0016` do ats-web). Por row: `declared_by_nir` (bool), `detection_status ∈ {pending, detected, not_detected}`, `doctor_disposition ∈ {pending, approved, denied}` + `doctor_reason` + `doctor_decided_at`. `procedure_type` validado contra o catálogo (D3) — tipo fora do catálogo é rejeitado na validação do modelo e nos serviços.

### D3 — Catálogo code-first: `apps/cases/procedure_catalog.py`

`@dataclass(frozen) ProcedureProfile(procedure_type, label, criteria_section, doctor_subtipo, anesthetic_support)` + registro ordenado dos 13 tipos (§2 do plano: seções S1–S8, subtipos angio×8/neuro/cardio×2/radio×2, flebografia→S1, carótidas→S2) + `CRITERIA_SECTIONS: dict[S1..S8]` com os thresholds como dados de código (consumidos pela policy do change 06) + flags estáticas de suporte anestésico (lista geral do §2). `get_procedure_profile(type)` **fail-fast** (sem fallback silencioso — divergência deliberada do ats-web, que tem fallback EDA). O comando `seed_procedure_catalog` é na prática **verificação** (valida coerência do registro — 13 tipos, seções 1–8, subtipos válidos — e reporta; idempotente por natureza). Novos tipos = mudança de código em change explícito (catálogo + profile + policy + prompts), sem migração de dados. *Desvio registrado do plano ("valores textuais no banco"): threshold/label ficam em código versionado; não há requisito de edição por UI no MVP — banco os introduziria como segunda fonte de verdade.*

### D4 — FSM: 17 estados como contrato

`CaseStatus(TextChoices)` com os 17 estados do plano §4 (renomeados vs ats-web: `R1_ACK_PROCESSING→PDF_EXTRACTING`, `EXTRACTING→ANONYMIZING`, `LLM_STRUCT→LLM_EXTRACTING`, `LLM_SUGGEST→LLM_SUMMARIZING`, `R2_POST_WIDGET` extinto, `WAIT_DOCTOR→AWAITING_DOCTOR`, `R3_POST_REQUEST→SCHEDULER_REQUESTED`, `WAIT_APPT→AWAITING_SCHEDULING`, `APPT_CONFIRMED/DENIED→SCHEDULING_CONFIRMED/DENIED`, `R1_FINAL_REPLY_POSTED→FINAL_REPLY_POSTED`, `WAIT_R1_CLEANUP_THUMBS→AWAITING_NIR_ACK`, `CLEANUP_RUNNING→CLEANING`). Transições com nomes de domínio (`start_pdf_extraction`, `complete_pdf_extraction`, `start_anonymization`, `complete_anonymization`, `start_llm_extraction`, `complete_llm_extraction`, `start_llm_summarization`, `complete_llm_summarization`, `fail_processing` [de qualquer estado de processamento → FAILED], `record_doctor_decision` [AWAITING_DOCTOR → DOCTOR_DENIED|DOCTOR_ACCEPTED], `post_final_reply` [DOCTOR_DENIED|SCHEDULING_*→FINAL_REPLY_POSTED], `request_scheduling` [DOCTOR_ACCEPTED→SCHEDULER_REQUESTED], `await_scheduling_confirmation`, `confirm_scheduling|deny_scheduling`, `post_final_reply`, `nir_acknowledge` [FINAL_REPLY_POSTED→AWAITING_NIR_ACK], `start_cleaning`, `complete_cleaning`). `DOCTOR_ACCEPTED` é estado real: o serviço de decisão médica grava o evento nele e avança `request_scheduling` na **mesma transação** (o estado intermediário fica observável na trilha). `FSMField(protected=True)` — atribuição direta de status fora das transições é rejeitada. **Guardrail**: changes futuros adicionam transições (ex.: reprocessamento no 04, reabertura por intercorrência no 08); estados só mudam por change explícito na spec.

### D5 — `CaseEvent`: trilha append-only como fonte de verdade

Espelho do ats-web: `case FK`, `timestamp` (indexado), `actor_type ∈ {user, system}`, `actor FK SET_NULL`, `actor_role` (papel ativo no momento), `event_type` (indexado), `payload JSON` enxuto. Gravação centralizada em `_record_event` (chamado por cada transição — padrão pending-event + persistência no mesmo `atomic`) e pelos serviços de procedimento/lock. Nenhuma operação de negócio altera ou remove eventos (append-only por contrato; sem API de edição).

### D6 — Locks/lease por caso

Campos no `Case` (espelho ats-web): `locked_by FK`, `locked_at`, `locked_until` (indexado), `lock_token UUID`, `lock_context` (ex.: `doctor_queue`, `worker_pipeline`), `lock_role`. Serviços em `apps/cases/locks.py`: `claim_case_lock(case, user, context, role, lease_seconds)` (claim condicional com `select_for_update` — livre **ou** lease expirada → novo token; ativo → `CaseLockConflict`), `assert_case_lock(case, token)`, `release_case_lock(case, token)`, `renew_case_lock`, `expire_stale_locks` (varredura de leases vencidas + evento). Lease default por env `CASE_LOCK_LEASE_SECONDS` (default 900). Eventos `CASE_LOCK_CLAIMED/RELEASED/EXPIRED/RENEWED` na trilha.

### D7 — `Case` enxuto, sem antecipação

Campos deste change: `case_id UUID pk`, `status FSMField`, `created_by FK PROTECT`, `agency_record_number` + `agency_record_extracted_at` (identidade para gate 04 e prior-case 06 — chegaram ao ats-web provando seu valor), timestamps, campos de lock (D6). **Não entram agora** (chegam com seus changes, em migrations incrementais): `pdf_file`/`extracted_text` (04), `structured_data`/`summary_text`/`priority_signals` (05/06), campos de decisão/agendamento (07/08), `corrects_case` (09), `scheduled_unit` (08).

### D8 — Comunicações por caso

`CaseCommunicationMessage` (espelho ats-web): `message_type ∈ {user, system}`, `author FK PROTECT (null p/ system)`, `author_role`, `body`, `source_event O2O → CaseEvent` (para system), `system_event_type`, `created_at`. Serviço `post_user_communication(case, user, body)` captura papel ativo da sessão; projeção sistêmica via signal `CaseEvent.post_save` → `create_system_communication_notice_for_event` para um conjunto inicial de eventos (`CASE_STATUS_AWAITING_DOCTOR`, `CASE_STATUS_DOCTOR_DENIED_FINAL`, `CASE_STATUS_SCHEDULING_*`) — conjunto constante em código, extensível nos changes de fluxo. Mensagens system não geram notificação/badge (notificações são change 11) e são append-only.

### D9 — Sem UI, sem admin

Este change entrega models + serviços + seed/verificação; telas chegam nos changes 04+; Django admin de casos fica de fora (não há requisito; `admin_ui` é change posterior). Toda a verificação é por testes de model/serviço.

### D10 — Dependência nova

`django-fsm-2==4.2.4` (pin; única dependência nova — ver D1). Sem workers django-q2 neste change (clusters chegam com 04/05/06).

## Riscos e mitigações

- **Licença/decisão de lib (D1)**: escalate ao dono antes do slice 002; default recomendado django-fsm-2 (MIT).
- **Concorrência real de claims**: `select_for_update` + update condicional; teste de dois claims concorrentes.
- **Catálogo como código**: deriva do documento clínico; teste de coerência (13/13, seções, subtipos) trava regressão; mudança clínica = change explícito.

## Fora de escopo

Upload/anexos/gate (04), anonimização/pseudônimos (05), prompts/schemas/LLM/policy/prior-case (06), filas/decisão UI (07), agendamento/unidade 2/intercorrência (08), encerramento/reenvio (09), notificações/dashboard (11).
