# Slice 002 — Fundação da intercorrência pós-encerramento

## 1. Identity

- Slice: `slice-002-fundacao-intercorrencia`
- Change: `paridade-ats-filas-historico-intercorrencia`
- Task: `tasks.md` § 2

## 2. Objective

Serviço de domínio + FSM + notificações que permitem reabrir um caso encerrado por
intercorrência: elegibilidade fail-closed com marcador persistente de disposição,
transição `CLEANED → AWAITING_SCHEDULING`, issue única ativa zerada em qualquer
encerramento, evento de auditoria com motivo+snapshot, fan-out de notificações — sem
superfície de UI (entregue no slice 003).

## 3. Read first

- `AGENTS.md`
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/proposal.md`
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/design.md` — D2/D3/D4/D8/D9
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/specs/post-closure-issue/spec.md` — R1/R2/R3 (cenários sem UI)
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/specs/notifications/spec.md` — MODIFIED (fan-out na reabertura)
- `apps/cases/models.py` — FSM (`@transition`), campos de agendamento, `scheduling_reopen_reason`
- `apps/cases/closure.py` — padrão atomic/lock; `_CLEANED_EMPTY_VALUES`; `purge_closed_case_data`; `administratively_close_case` (integração D9)
- `apps/scheduler/services.py` — `reopen_scheduling_after_incident` (padrão a espelhar) e `confirm_case_scheduling` (origem `AWAITING_SCHEDULING` aceita)
- `apps/accounts/notifications.py` — dispatcher de marcos (`_create_scheduling_reopened_notification`, `_create_scheduler_notifications`)
- `apps/accounts/tests/` — padrões de teste de notificações (destinatários, idempotência)
- `apps/cases/event_trail.py` — catálogo de rótulos/badges
- `/projects/dev/ats-web/apps/cases/services.py` — `open_post_acceptance_issue`/`is_post_acceptance_issue_eligible` (referência de paridade)

## 4. Requirements

- R1: elegibilidade — `CLEANED` + agendamento anterior confirmado na unidade 1 + dados
  retidos (coluna `clinical_data_disposed_at` NULA — D2) + sem issue ativa; motivo legível
  para cada inelegibilidade (dado disposto, unidade 2, issue ativa, não encerrado, sem
  agendamento confirmado).
- R2: abertura atômica — `select_for_update`, revalidação sob lock, transição
  `reopen_post_closure` (`CLEANED → AWAITING_SCHEDULING`), limpeza dos 5 campos de
  agendamento com snapshot prévio no evento `CASE_POST_CLOSURE_ISSUE_OPENED` (payload =
  motivo de texto livre + snapshot administrativo unidade/data/hora/local — mesmo
  precedente do evento de reabertura vigente; motivo também interpolado na comunicação da
  thread), `scheduling_reopen_reason` persistido; motivo ausente/vazio ou caso inelegível
  não escreve NADA.
- R3: issue única e encerramento — coluna compacta de status da issue zerada em QUALQUER
  chegada a `CLEANED` (ciência do novo ciclo OU encerramento administrativo), no mesmo
  atomic, com evento de auditoria do fechamento da issue; reabertura subsequente recusada
  com motivo; corrida abertura×encerramento-administrativo resolvida pelo lock.
- R4: notificações (delta `notifications` MODIFIED) — o marco de reabertura
  (`CASE_STATUS_AWAITING_SCHEDULING` com `reason`) passa a notificar o criador E fazer
  fan-out dos agendadores ativos (preview fixo novo), cobrindo reabertura pré-ciência e
  pós-encerramento; idempotente por `unique(recipient,event)` para ambos os destinatários;
  falha na notificação jamais bloqueia a transição.
- Evidência: testes de serviço em `apps/cases/tests/` (R1–R3, incl. corrida concorrente) +
  testes de destinatários/idempotência em `apps/accounts/tests/` (R4).

## 5. Expected blast radius

- Esperado: `apps/cases/models.py` (transição + campos), migration nova (issue +
  `clinical_data_disposed_at` + backfill + índices), `apps/cases/closure.py` (serviço +
  elegibilidade + gravação do marcador na minimização e na purga + integração D9 no
  encerramento administrativo), `apps/accounts/notifications.py` (dispatcher do marco de
  reabertura + preview novo), `apps/cases/event_trail.py` (rótulo/badge dos eventos
  novos), testes dos dois apps.
- Sensível (não mudar sem escalada): fluxo da ciência (`acknowledge_case_receipt`),
  semântica da minimização/purga (`_clean_acknowledged_clinical_data` incondicional,
  `purge_closed_case_data`), marcos de notificação existentes além do de reabertura,
  FSM de outras transições.
- Comportamento anterior preservado: `reopen_scheduling_after_incident` (desmarcar em
  `FINAL_REPLY_POSTED`) intocado além do efeito do R4 no marco compartilhado; encerramento
  administrativo preservado exceto a zeragem da issue (D9).

## 6. Failing-before plan

- Targeted tests: elegibilidade (5 ramos de R1) + abertura (R2, incl. escrita-zero em
  motivo ausente; presença de motivo+snapshot no payload) + issue única/admin-closure (R3) +
  destinatários e idempotência do fan-out (R4 — hoje o marco notifica só o criador).
- Expected failure: `ImportError`/`AttributeError` no serviço/transição; teste de
  destinatários falha (sem notificação aos agendadores); registrar antes de implementar.

## 7. Implementation constraints

- Migration única do slice, aditiva: campos de issue + `clinical_data_disposed_at` +
  backfill (`CLEANED` com `summary_text` vazio ⇒ disposed — justificativa D2) + índices
  (`patient_name`, `agency_record_number`).
- O marcador `clinical_data_disposed_at` é gravado no MESMO atomic da minimização
  (entry point com flag) e da purga — nunca lido como proxy de campos.
- Histórico de ciclos somente na trilha de eventos (D3) — sem `cycle_id` nem os ~12
  campos do ats-web.
- Payloads de evento PHI-free por construção (código + snapshot administrativo).
- Motivo é texto livre obrigatório, reuso do padrão do desmarcar (`SchedulerReopenForm`)
  — decisão do dono 2026-11-21 (design D3): sem catálogo fechado, sem campo de mensagem
  separado; o par reason+message do ats-web colapsa no motivo único.
- Carimbos `scheduling_decided_*`/`doctor_decided_*` NÃO fazem parte deste slice (004/005).

## 8. Validation

- Focused: `uv run pytest apps/cases -k "post_closure or reopen or disposed"` +
  `uv run pytest apps/accounts -k "notif"` (nomes conforme suíte) + teste de trilha.
- Project gates: bateria completa de `AGENTS.md` § 2 (após a migration, `pytest --create-db`
  uma vez — quirk do harness).
- Runtime: não exigida (serviço puro).

## 9. Acceptance criteria

- [ ] R1: 5 ramos de elegibilidade testados com motivos legíveis; detecção por coluna
      `clinical_data_disposed_at` (não por proxy).
- [ ] R2: abertura transaciona status+campos+evento+comunicação; falha não escreve nada;
      payload com motivo + snapshot administrativo (precedente do evento vigente).
- [ ] R3: issue ativa bloqueia reabertura; zerada na ciência E no encerramento
      administrativo (mesmo atomic, evento próprio); corrida testada sob lock.
- [ ] R4: criador + agendadores ativos notificados na reabertura (pré-ciência e
      pós-encerramento); idempotente; falha não bloqueia.
- [ ] Migration aditiva aplicável (backfill do marcador verificado em teste).
- [ ] Quality gate completo verde.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: a transição `CLEANED → AWAITING_SCHEDULING`
conflitar com guards existentes do FSM; se a reentrada em `AWAITING_SCHEDULING` exigir
mudar `confirm_case_scheduling`; se a extensão do marco de reabertura colidir com outros
marcos/rotas de notificação além do previsto; se o backfill do marcador revelar casos
`CLEANED` com sumário vazio E agendamento confirmado (premissa D2 violada).

## 11. Evidence report

`/tmp/hmd-slice-002-report.md` (estrutura canônica do skill).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
