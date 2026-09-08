# Slice 003: App scheduler — fila completa, detalhe limitado e formulários

## Objetivo

UI do estágio de agendamento: fila do agendador (abas
aguardando/processados, FIFO, paginada, sem filtro por unidade), detalhe do
caso **limitado ao necessário** (identificação real + decisões médicas +
agendamento + thread; sem artefatos clínicos nem PDF) e formulários de
confirmar (unidade 1|2 + data/hora + local), negar (motivo) e desmarcar
(intercorrência, unidade 1 apenas).

## Contexto necessário

- Slices 001–002 entregues: `apps/scheduler/services.py`
  (`confirm_case_scheduling`, `deny_case_scheduling`,
  `reopen_scheduling_after_incident`; erros nomeados por validação) e campos
  `Case.scheduled_*`.
- Padrões do change 07 a replicar (leia os fontes): `apps/doctor/views.py`
  (fila com abas + Paginator + guard `role_required` + nav),
  `apps/doctor/tests/conftest.py` (fixtures login+papel ativo),
  `apps/doctor/forms.py` (form por caso com validação), `templates/base.html`
  (nav por `active_role`).
- `apps/accounts/decorators.py::role_required("scheduler", "admin")`
  (anônimo → redirect login; outro papel ativo → 403).
- `apps/cases/procedures.py::get_declared_procedure_types`;
  `CaseProcedure.doctor_disposition/doctor_reason`; comunicação da thread:
  `case.communication_messages` (padrão de render do
  `templates/intake/case_detail.html`).
- **One-liner re-identificado (design D3, decisão do dono 2026-09-08)**:
  `apps/anonymization/reidentify.py::reidentify_text(case, text)` — a mesma
  função do presenter médico (`apps/doctor/presenters.py:528`); usar a
  **primeira linha não-vazia** de `case.summary_text`, re-identificar SÓ na
  renderização, nunca persistir.
- **PDF pós-decisão (design D3)**: padrão da view de PDF do médico do change
  07 (`FileResponse` + `Cache-Control: no-store`, gate por caso/usuário) e
  semântica do gate `_get_scheduler_processed_case_or_404` do ats-web
  (`/projects/dev/ats-web/apps/scheduler/views.py` — somente-leitura,
  referência).
- Design D3/D4 (`openspec/changes/scheduler-multi-unit/design.md`) —
  **detalhe limitado**: identificação (nome/nascimento/nº ocorrência) REAL +
  diagnóstico resumido + decisões médicas + agendamento + comunicações; SEM
  presenter médico (demais artefatos clínicos), SEM PDF na fila/pré-decisão.
- Transições FSM levantam `TransitionNotAllowed` em concorrência → view
  traduz para mensagem + redirect (padrão D4/D5; nunca 500).

## Requisitos verificáveis

- **R1** Urls em `/scheduler/` + `config/urls.py` + nav no `base.html` para
  `active_role in scheduler,admin` (o app `apps.scheduler`, `apps.py` e o
  registro em `INSTALLED_APPS` já vieram do slice 001).
- **R2** Fila `scheduler:queue`: abas `aguardando` (= `SCHEDULER_REQUESTED`
  **ou** `AWAITING_SCHEDULING` — pedidos novos e casos reabertos por
  intercorrência) e `processados` (=
  `SCHEDULING_CONFIRMED|SCHEDULING_DENIED|FINAL_REPLY_POSTED`); FIFO
  `created_at`; paginada; cards com identificação, tipos declarados e unidade
  quando definida; **sem filtro por unidade**. Guard R2: anônimo → redirect;
  `nir`/`doctor`/`manager` → 403; composição `scheduler+manager` com ativo
  `scheduler` → 200.
- **R3** Detalhe `scheduler:case_detail`: contexto com identificação real
  (`Case.patient_name`, `Case.patient_birth_date`, `Case.agency_record_number`
  — nomes reais dos campos), **diagnóstico resumido** (primeira linha
  não-vazia de `case.summary_text` re-identificada na renderização via
  `reidentify_text` — sem tokens na página; demais linhas do resumo
  ausentes), procedimentos declarados c/ disposição médica e motivo das
  negativas, dados de agendamento atuais e comunicações da thread;
  **nenhum conteúdo** de `structured_data`/`policy_result`/
  `suggested_action` na página (assert de ausência); sem link para PDF.
- **R4** Form confirmar (`SchedulerConfirmForm`: unidade choice 1|2,
  data/hora futura, local; validação espelhando o serviço) + POST →
  `confirm_case_scheduling` → redirect ao detalhe com flash e resposta
  visível na thread; form negar (motivo obrigatório) → `deny_case_scheduling`;
  botão **desmarcar** (motivo obrigatório) aparece SOMENTE em
  `FINAL_REPLY_POSTED` com `scheduled_unit == 1` → `reopen_scheduling_after_incident`;
  em unidade 2 exibe banner "intercorrência desabilitada" (sem botão).
- **R7** View de PDF `scheduler:case_pdf`: serve `case.pdf_file` (FileResponse,
  `application/pdf`, `Cache-Control: no-store`) SOMENTE quando
  `case.scheduled_by == request.user` E status ∈ {`SCHEDULING_CONFIRMED`,
  `SCHEDULING_DENIED`, `FINAL_REPLY_POSTED`, `AWAITING_NIR_ACK`} (404
  fail-closed caso contrário — inclusive caso reaberto por intercorrência,
  cujo `scheduled_by` foi limpo); link exibido no detalhe apenas nessa
  condição; decorator `role_required` como as demais views.
- **R5** Erros do serviço (validação/estado/concorrência) → mensagem de erro
  + redirect/re-render com form; **nunca 500**; nenhuma escrita parcial.
- **R6** Testes de view: fila (abas/FIFO — inclui caso reaberto em
  `AWAITING_SCHEDULING` na aba aguardando —/guard×papéis/paginação), detalhe
  (conteúdo presente — incluindo one-liner re-identificado sem tokens — e
  artefatos clínicos ausentes), confirmar unidade 1 e 2
  (texto da resposta na thread), negar com/sem motivo, desmarcar unidade 1
  (caso volta à aba aguardando em `AWAITING_SCHEDULING`) e bloqueio
  unitário 2 (sem botão + POST direto recusado com erro), estado errado no
  POST (sem 500), PDF (R7: 200 caso próprio pós-decisão / 404 de outro
  agendador e de caso reaberto).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/scheduler/{apps,urls}.py`, `config/urls.py`, `templates/base.html` | `test_nav_visible_for_scheduler` |
| R2 | `apps/scheduler/views.py` | `test_queue_awaiting_default`, `test_queue_processed_tab`, `test_queue_forbidden_*`, `test_anonymous_redirects_login` |
| R3 | `apps/scheduler/{views,presenters}.py`, `templates/scheduler/case_detail.html` | `test_detail_shows_identification_and_decisions`, `test_detail_one_liner_reidentified`, `test_detail_has_no_clinical_artifacts` |
| R4 | `apps/scheduler/forms.py`, `templates/scheduler/{case_detail,decide-like forms}` | `test_confirm_unit_1_flow`, `test_confirm_unit_2_exact_reply`, `test_deny_flow`, `test_reopen_button_only_unit_1`, `test_reopen_unit_2_post_rejected` |
| R7 | `apps/scheduler/views.py` (rota `case_pdf`) | `test_pdf_own_processed_case_200`, `test_pdf_other_scheduler_404`, `test_pdf_reopened_case_404` |
| R5 | `apps/scheduler/views.py` | `test_wrong_state_post_no_500` |
| R6 | `apps/scheduler/tests/test_views.py` (+conftest se preciso) | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/scheduler/{urls,views,forms,presenters}.py
  - apps/scheduler/tests/{conftest,test_views}.py
  - templates/scheduler/{queue,case_detail}.html
  - config/urls.py
  - templates/base.html            # nav

out_of_scope:
  - mudanças em apps/cases (serviços/FSM dos slices 001–002 são consumidos como estão)
  - UI do NIR (thread já visível no intake do change 04)
  - nir_acknowledge em diante (change 09); dashboard/notificações (11)
  - estilos novos além do tema/Bootstrap existentes
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/scheduler/tests/test_views.py`
- Falha esperada: `ImportError`/`AttributeError` em `scheduler:queue`
  (urls/views ainda não existem).

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/scheduler/tests/ apps/cases/tests/
  apps/intake/tests/` — exit 0 (regressão: FSM + thread do NIR).
- `uv run ruff check apps/scheduler config && uv run ruff format --check apps/scheduler config`
- `uv run mypy .`

## Critérios de aceitação

- [ ] R1–R7 comprovados; página do detalhe não expõe NENHUM artefato
      clínico além do one-liner re-identificado (assert de ausência com
      artefatos populados) nem PDF na fila/pré-decisão
- [ ] One-liner re-identificado só na renderização (nunca persistido, sem
      tokens na página)
- [ ] PDF exclusivo de caso processado pelo próprio agendador pós-decisão
      (404 para os demais; caso reaberto não expõe)
- [ ] Texto da resposta unidade 2 exato na thread após confirmar via UI
- [ ] Desmarcar só existe para unidade 1; POST direto na unidade 2 recusado
- [ ] Gate parcial do slice verde
