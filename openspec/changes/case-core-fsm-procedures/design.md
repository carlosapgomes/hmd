# Design: case-core-fsm-procedures

## Contexto

Generalização do núcleo comprovado do `ats-web` (recon: `temp/research/ats-web-recon.md` seções 3–4 e 8) para 13 tipos de procedimento. Fontes clínicas: `temp/plano-implementacao-hmd.md` §2 (catálogo/thresholds) e §4 (FSM 17 estados); `temp/parametrosHMD.md` (documento original). Pesquisa de FSM: `temp/research/viewflow-fsm.md`.

### D1 — Biblioteca de FSM: **`django-fsm-2==4.2.4`** (decisão confirmada pelo dono, 2026-09-07)

O plano original dizia "viewflow.fsm". A pesquisa (`temp/research/viewflow-fsm.md`) revelou: (a) o pacote é `django-viewflow==2.4.0`, ativo e compatível com Django 5.2/Py3.13, porém **AGPLv3+** — exige validação institucional de licença; (b) sua API (flow class separada + hooks `on_success`, sem signals Django) **diverge do padrão do ats-web**, invalidando parcialmente as referências de código que o HMD clona; (c) **`django-fsm-2==4.2.4`** (django-commons) é **MIT**, drop-in da API django-fsm (`@transition`/`FSMField(protected=True)`/signals `pre_transition`/`post_transition`/`ConcurrentTransitionMixin`), Django 5.2/Py3.13 — mantém as referências do ats-web válidas ~1:1. **Decisão do dono (2026-09-07): django-fsm-2** — a sucessão oficial pelo autor original (viewflow) foi avaliada e preterida em favor de MIT + fidelidade às referências. Registrada em ADR-0005 e no plano §4.

### D2 — Case neutro (padrão ADR-0004 do ats-web)

A dimensão de procedimento vive **exclusivamente** em `CaseProcedure` (1–N rows por caso; `UniqueConstraint(case, procedure_type)`; sem `Case.exam_type` — lição da migration `cases.0016` do ats-web). Por row: `declared_by_nir` (bool), `detection_status ∈ {pending, detected, not_detected}`, `doctor_disposition ∈ {pending, approved, denied}` + `doctor_reason` + `doctor_decided_at`. `procedure_type` validado contra o catálogo (D3) — tipo fora do catálogo é rejeitado na validação do modelo e nos serviços.

### D3 — Catálogo code-first: `apps/cases/procedure_catalog.py`

`@dataclass(frozen) ProcedureProfile(procedure_type, label, criteria_section, doctor_subtipo, anesthetic_support)` + registro ordenado dos 13 tipos (§2 do plano: seções S1–S8, subtipos angio×8/neuro/cardio×2/radio×2, flebografia→S1, carótidas→S2) + `CRITERIA_SECTIONS: dict[S1..S8]` com os thresholds como dados de código (consumidos pela policy do change 06) + flags estáticas de suporte anestésico (lista geral do §2). `get_procedure_profile(type)` **fail-fast** (sem fallback silencioso — divergência deliberada do ats-web, que tem fallback EDA). O comando `seed_procedure_catalog` é na prática **verificação** (valida coerência do registro — 13 tipos, seções 1–8, subtipos válidos — e reporta; idempotente por natureza). Novos tipos = mudança de código em change explícito (catálogo + profile + policy + prompts), sem migração de dados. *Desvio registrado do plano ("valores textuais no banco"): threshold/label ficam em código versionado; não há requisito de edição por UI no MVP — banco os introduziria como segunda fonte de verdade.*

### D4 — FSM: 17 estados como contrato

`CaseStatus(TextChoices)` com os 17 estados do plano §4 (renomeados vs ats-web: `R1_ACK_PROCESSING→PDF_EXTRACTING`, `EXTRACTING→ANONYMIZING`, `LLM_STRUCT→LLM_EXTRACTING`, `LLM_SUGGEST→LLM_SUMMARIZING`, estado `R2_POST_WIDGET` do ats-web deliberadamente extinto, `WAIT_DOCTOR→AWAITING_DOCTOR`, `R3_POST_REQUEST→SCHEDULER_REQUESTED`, `WAIT_APPT→AWAITING_SCHEDULING`, `APPT_CONFIRMED/DENIED→SCHEDULING_CONFIRMED/DENIED`, `R1_FINAL_REPLY_POSTED→FINAL_REPLY_POSTED`, `WAIT_R1_CLEANUP_THUMBS→AWAITING_NIR_ACK`, `CLEANUP_RUNNING→CLEANING`; nota: o ats-web materializa 18 estados — o HMD fecha em 17 por desenho). **Tabela completa de transições** (operação → source → target):

| Operação | Source | Target |
| --- | --- | --- |
| `start_pdf_extraction` | `NEW` | `PDF_EXTRACTING` |
| `complete_pdf_extraction` | `PDF_EXTRACTING` | `ANONYMIZING` |
| `start_anonymization` | `ANONYMIZING` | `ANONYMIZING` (self, início do worker) |
| `complete_anonymization` | `ANONYMIZING` | `LLM_EXTRACTING` |
| `start_llm_extraction` | `LLM_EXTRACTING` | `LLM_EXTRACTING` (self) |
| `complete_llm_extraction` | `LLM_EXTRACTING` | `LLM_SUMMARIZING` |
| `start_llm_summarization` | `LLM_SUMMARIZING` | `LLM_SUMMARIZING` (self) |
| `complete_llm_summarization` | `LLM_SUMMARIZING` | `AWAITING_DOCTOR` |
| `fail_processing` | `PDF_EXTRACTING, ANONYMIZING, LLM_EXTRACTING, LLM_SUMMARIZING` | `FAILED` |
| `record_doctor_decision` | `AWAITING_DOCTOR` | `DOCTOR_DENIED` \| `DOCTOR_ACCEPTED` (target dinâmico pela decisão) |
| `request_scheduling` | `DOCTOR_ACCEPTED` | `SCHEDULER_REQUESTED` |
| `await_scheduling_confirmation` | `SCHEDULER_REQUESTED` | `AWAITING_SCHEDULING` |
| `confirm_scheduling` | `AWAITING_SCHEDULING` | `SCHEDULING_CONFIRMED` |
| `deny_scheduling` | `AWAITING_SCHEDULING` | `SCHEDULING_DENIED` |
| `post_final_reply` | `DOCTOR_DENIED, SCHEDULING_CONFIRMED, SCHEDULING_DENIED` | `FINAL_REPLY_POSTED` |
| `nir_acknowledge` | `FINAL_REPLY_POSTED` | `AWAITING_NIR_ACK` |
| `start_cleaning` | `AWAITING_NIR_ACK` | `CLEANING` |
| `complete_cleaning` | `CLEANING` | `CLEANED` |

Self-transitions de início de worker existem para registrar o evento de início sem mudar de estado (padrão django-fsm: source=target válido); cada transição é testada individualmente. `FSMField(protected=True)` — atribuição direta de status é rejeitada. **Guardrail**: changes futuros adicionam transições (ex.: reprocessamento no 04, reabertura por intercorrência no 08); estados só mudam por change explícito na spec.

### D5 — `CaseEvent`: trilha append-only como fonte de verdade

Base do ats-web com **duas divergências deliberadas**: (1) HMD acrescenta `actor_role` (o ats-web não tem — o papel ativo importa no HMD por causa do multi-role) e usa `actor_type ∈ {user, system}` (o ats-web usa `human`); (2) **gravação direta** — cada transição/serviço cria o `CaseEvent` explicitamente dentro da própria operação transacionada (`save` + `create` no mesmo `atomic`), **sem** o padrão pending-event + signal `Case.post_save` do ats-web (menos peças móveis, mesma garantia append-only; o signal `CaseEvent.post_save` continua existindo só para a projeção de comunicações, D8). Campos: `case FK`, `timestamp` (indexado), `actor_type`, `actor FK SET_NULL`, `actor_role`, `event_type` (indexado), `payload JSON` enxuto. **`actor_role` é parâmetro explícito** de toda operação auditada (transições recebem `*, user, role`; views dos changes 04+ extraem o papel ativo da sessão — padrão `apps/accounts`; workers passam `role="system"`). **Tipos canônicos** em `apps/cases/events.py` (enum/constantes únicos, consumidos por FSM, serviços, locks e projeção): transições `CASE_STATUS_<TARGET>` (ex.: `CASE_STATUS_AWAITING_DOCTOR`, `CASE_STATUS_DOCTOR_DENIED`, `CASE_STATUS_SCHEDULING_CONFIRMED`); operações `CASE_PROCEDURES_DECLARED`, `CASE_PROCEDURES_DETECTED`, `CASE_DOCTOR_DECISIONS_RECORDED`; locks `CASE_LOCK_CLAIMED/RELEASED/RENEWED/EXPIRED`.

### D6 — Locks/lease por caso

Campos no `Case` (espelho ats-web): `locked_by FK`, `locked_at`, `locked_until` (indexado), `lock_token UUID`, `lock_context` (ex.: `doctor_queue`, `worker_pipeline`), `lock_role`. Serviços em `apps/cases/locks.py` (**reorganização HMD** — no ats-web vivem em `services.py`; `renew` com evento e `expire_stale_locks()` genérica são **extensões HMD**): `claim_case_lock(case, *, user, context, role, lease_seconds=None)` (dentro de `transaction.atomic()` + `select_for_update` — livre **ou** lease expirada (grava `CASE_LOCK_EXPIRED` e assume) → novo token; ativo de outro ator → `CaseLockConflictError` sem alterar nada), `assert_case_lock(case, token)`, `release_case_lock(case, token)` (evento `CASE_LOCK_RELEASED`), `renew_case_lock` (evento `CASE_LOCK_RENEWED`), `expire_stale_locks()` (varredura + `CASE_LOCK_EXPIRED`). Lease default por env `CASE_LOCK_LEASE_SECONDS` (**300s**, alinhado ao ats-web; sem as variantes por papel do ats-web). **Integração com mutações**: o mecanismo é o contrato deste change; os consumers ligam `assert_case_lock` nas mutações dos seus fluxos (07+/workers 04–06) — "mutação sob lock exige token" vale para os fluxos que operam com lock, não para toda escrita do sistema.

### D7 — `Case` enxuto, sem antecipação

Campos deste change: `case_id UUID pk`, `status FSMField`, `created_by FK PROTECT`, `agency_record_number` + `agency_record_extracted_at` (identidade para gate 04 e prior-case 06 — chegaram ao ats-web provando seu valor), timestamps, campos de lock (D6). **Não entram agora** (chegam com seus changes, em migrations incrementais): `pdf_file`/`extracted_text` (04), `structured_data`/`summary_text`/`priority_signals` (05/06), campos de decisão/agendamento (07/08), `corrects_case` (09), `scheduled_unit` (08).

### D8 — Comunicações por caso

`CaseCommunicationMessage` (espelho ats-web): `message_type ∈ {user, system}`, `author FK PROTECT (null p/ system)`, `author_role`, `body`, `source_event O2O → CaseEvent` (para system), `system_event_type`, `created_at`. Serviço `post_user_communication(case, *, user, role, body)` com papel explícito (**serviço novo do HMD**; views extraem da sessão nos changes 04+); projeção sistêmica via signal `CaseEvent.post_save` → `create_system_communication_notice_for_event` para o conjunto inicial de **tipos canônicos** (D5): `CASE_STATUS_AWAITING_DOCTOR`, `CASE_STATUS_DOCTOR_DENIED`, `CASE_STATUS_SCHEDULING_CONFIRMED`, `CASE_STATUS_SCHEDULING_DENIED` — constantes em `apps/cases/events.py`, extensível nos changes de fluxo. Mensagens system não geram notificação/badge (notificações são change 11) e são append-only.

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
