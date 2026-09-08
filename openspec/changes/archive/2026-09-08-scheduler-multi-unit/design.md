# Design: scheduler-multi-unit

Referências: plano §4 (regras de unidade/fluxo), §10; FSM e eventos do change
03 (`_run_transition` com `*, user, role`; eventos `CASE_STATUS_*`);
comunicações do change 03 (`post_user_communication`; NIR já as vê no detalhe
do intake); padrões de fila/access do change 07 (`DoctorAccess`,
`role_required`); ats-web `apps/scheduler/` (somente-leitura — padrões de
fila/formulário de confirmação).

## D1 — Campos de agendamento no `Case` (migration única `cases/0008`)

Todos opcionais/nulos (rows existentes não quebram):

- `scheduled_unit: PositiveSmallIntegerField(choices=1|2, null=True, blank=True)`
- `scheduled_datetime: DateTimeField(null=True, blank=True)` (aware, tz local)
- `scheduled_location: CharField(max_length=200, blank=True)`
- `scheduled_by: FK(User, SET_NULL, null=True, related_name="cases_scheduled")`
- `scheduled_decided_at: DateTimeField(null=True, blank=True)`
- `scheduling_denial_reason: TextField(blank=True)`
- `scheduling_reopen_reason: TextField(blank=True)`

Nenhum evento canônico novo: transições usam `CASE_STATUS_*`; a intercorrência
é a transição de reabertura com payload `{source: FINAL_REPLY_POSTED,
reason}`. A projection de system-notices (`SYSTEM_COMMUNICATION_TEXTS`) não é
estendida — a resposta ao NIR é **user message** autoral (D2), não notice.

## D2 — Serviços transacionais em `apps/scheduler/services.py`

Padrão do change 03 (`select_for_update` + tudo no mesmo atomic). Dono dos
serviços é o app scheduler (o núcleo `apps/cases` fica com FSM/locks/eventos;
decisão espelha o change 07, onde a UI vive no app do papel):

- **`confirm_case_scheduling(case, *, unit, scheduled_datetime,
  scheduled_location, user, role)`**: valida estado ∈ {`SCHEDULER_REQUESTED`,
  `AWAITING_SCHEDULING`}, `unit ∈ {1,2}`, data/hora não no passado;
  encadeia até `post_final_reply` com **branch por estado** (`await_scheduling_confirmation`
  tem source único `SCHEDULER_REQUESTED` na FSM do change 03): em
  `SCHEDULER_REQUESTED` dispara `await_scheduling_confirmation` →
  `confirm_scheduling` → `post_final_reply` (3 eventos `CASE_STATUS_*`); em
  `AWAITING_SCHEDULING` (caso reaberto por intercorrência, D2 abaixo) **pula
  o `await`** e dispara `confirm_scheduling` → `post_final_reply` (2
  eventos). No mesmo atomic persiste os 5 campos (`scheduled_unit`,
  `scheduled_datetime`, `scheduled_location`, `scheduled_by=user`,
  `scheduled_decided_at=now`) e posta na thread a resposta final ao NIR
  (user message, autor = agendador):
  - unidade 1: `Agendamento confirmado — {local}, {data/hora local}. Comparecer
    com documentos e exames.`
  - unidade 2: `Recusar o relatório — caso agendado na Unidade 2, que
    comunicará a Secretaria` (texto exato do plano §4, sem ponto final)
- **`deny_case_scheduling(case, *, reason, user, role)`**: motivo obrigatório
  (não vazio); mesmo branch por estado do confirmar (em `AWAITING_SCHEDULING`
  pula o `await`); encadeia até `deny_scheduling` → `post_final_reply` +
  persiste `scheduling_denial_reason` + posta resposta com o motivo.
- **`reopen_scheduling_after_incident(case, *, reason, user, role)`**
  (intercorrência): válido apenas em `FINAL_REPLY_POSTED` **e**
  `scheduled_unit == 1` (unidade 2 → erro nomeado); motivo obrigatório;
  nova transição `reopen_scheduling` (`FINAL_REPLY_POSTED →
  AWAITING_SCHEDULING`), limpa `scheduled_*` (unit/datetime/location/by/
  decided_at; `scheduled_by` mantém quem reabriu? Não — limpa, o histórico
  vive nos eventos), persiste `scheduling_reopen_reason`, posta comunicação
  ao NIR ("Intercorrência — agendamento desmarcado ({motivo}); caso retorna à
  fila para novo agendamento"). Re-confirmação posterior usa o mesmo serviço
  de confirmar (fonte `AWAITING_SCHEDULING` aceita).

Respostas de texto são constantes do módulo (dados, não strings espalhadas).

## D3 — O que o scheduler vê: alinhado ao ats-web (decisão do dono 2026-09-08)

Recon do ats-web (somente-leitura) confirmou que o scheduler original vê o
**mínimo necessário** — identificação real + one-liner de "diagnóstico" +
projeção aprovada — e o dono decidiu **alinhar o HMD a isso**. O detalhe do
agendador renderiza, portanto:

1. **Identificação real**: `patient_name`, `patient_birth_date`,
   `agency_record_number`, unidade de origem.
2. **Diagnóstico resumido**: a **primeira linha não-vazia** de
   `case.summary_text`, re-identificada **na renderização** com
   `reidentify_text(case, ...)` (`apps/anonymization/reidentify.py` — a
   mesma função do presenter médico; equivalente ao one-liner do card do
   ats-web). Nunca persistida re-identificada; demais linhas do resumo,
   `structured_data`, `policy_result` e `suggested_action` seguem
   invisíveis (assert de ausência).
3. **Decisões médicas por procedimento** (aprovadas **e** negadas, com
   motivos) + dados de agendamento atuais + thread de comunicações.

**PDF pós-decisão, caso próprio**: os documentos originais do relatório
(`CaseDocument` — **multi-PDF por design**: o relatório SESAB chega em 1–N
PDFs com `position`) servidos por view própria por `position` (`FileResponse`
+ `Cache-Control: no-store` — padrão do `doctor:case_pdf` do change 07,
`apps/doctor/urls.py`/`views.py`) SOMENTE quando `scheduled_by ==
request.user` E status ∈ {`SCHEDULING_CONFIRMED`, `SCHEDULING_DENIED`,
`FINAL_REPLY_POSTED`, `AWAITING_NIR_ACK`} (semântica do
`_get_scheduler_processed_case_or_404` do ats-web — decisão do dono).
Sem link na fila/pré-decisão; na condição satisfeita o detalhe lista links
para **todos** os documentos do caso (por `position`); caso reaberto por
intercorrência (`scheduled_by` limpo) → 404 fail-closed. Servir só o
primeiro documento entregaria relatório incompleto quando N>1 — por isso a
rota por `position`. Os PDFs são domínio humano (como para o médico): nunca
são enviados à LLM nem ao pipeline anonimizado.

## D4 — Fila do agendador

`/scheduler/` (`role_required("scheduler", "admin")`; anônimo → redirect;
outros papéis → 403 — matriz por papel ativo, mesma semântica do change 07):
abas `aguardando` (= `SCHEDULER_REQUESTED` **ou** `AWAITING_SCHEDULING` —
pedidos novos e casos reabertos por intercorrência precisam ser visíveis
para re-agendar; default) e `processados`
(= `SCHEDULING_CONFIRMED|SCHEDULING_DENIED|FINAL_REPLY_POSTED` — os dois
primeiros transitórios, incluídos inofensivos), FIFO por `created_at`,
paginada (padrão Paginator do change 07), badge por tipo/procedimento e
unidade quando definida. **Sem filtro por unidade** (fila completa, plano §4).
Detalhe (`/scheduler/case/<id>/`): identificação (D3), decisões médicas por
procedimento, comunicações da thread e ações por estado —
`SCHEDULER_REQUESTED`/`AWAITING_SCHEDULING`: form confirmar (unidade 1|2 +
data/hora + local) ou negar (motivo); `FINAL_REPLY_POSTED` **com
`scheduled_unit == 1`**: botão desmarcar (intercorrência, motivo);
`FINAL_REPLY_POSTED` com unidade 2: banner "intercorrência desabilitada".

## D5 — Sem locks de posse e sem signals novos

Ações são POST único atômico (`select_for_update` + transições); concorrência
cai em `TransitionNotAllowed` → mensagem + redirect (padrão D4 do change 07).
Nenhum signal/on_commit: nada assíncrono neste estágio (a projeção de
comunicações por `CaseEvent.post_save` existe e não muda).

## D6 — Migrações, navegação e limites

Migration única `0008_case_scheduling` (D1); nav do `base.html` por papel
ativo `scheduler|admin`; `INSTALLED_APPS` += `apps.scheduler`. Fora: ack do
NIR (09), resposta de negativa médica (09), UI do NIR além da thread já
existente.
