# Slice 007 — PDF/texto do agendador por decisão de agendamento (equipe) + manual

## 1. Identity

- Slice: `slice-007-pdf-agendador-decisao`
- Change: `visibilidade-por-equipe`
- Task: `tasks.md` S7 (depende do slice 006)

## 2. Objective

O gate de `Visualizar PDF Original`/`Texto Extraído do PDF` no detalhe do
agendador (ativo e histórico) passa a **decisão de agendamento + estado** —
qualquer agendador da equipe vê e serve; negativa médica direta e casos fora do
gate continuam 404. Manual do agendador concluído.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/visibilidade-por-equipe/design.md` — D7 (guarda OBRIGATÓRIA de decisão; racional da negativa médica), D2 (manual), D6, D8
- `openspec/changes/visibilidade-por-equipe/specs/scheduling/spec.md` — MODIFIED «Visão do agendador limitada ao necessário» e «Histórico do agendador»
- `openspec/changes/visibilidade-por-equipe/specs/pdf-original-viewer/spec.md` — MODIFIED «Guards de serve e viewer por papel» (agendador)
- `apps/scheduler/views.py` — `PDF_ALLOWED_STATUSES` (:128-135) e os 4 pontos do gate `can_view_pdf` (:466 `case_detail`, :528 `pdf_viewer`, :824 `case_pdf`, :874 `historical_case_detail`)
- `apps/cases/closure.py` — `post_doctor_denial_reply` (negativa médica → `FINAL_REPLY_POSTED` sem decisão de agendamento)
- `templates/scheduler/` — cards de PDF/texto; `templates/accounts/manual.html` (seção Agendador)
- Testes: `apps/scheduler/tests/test_detail_cards.py`, `test_pdf_viewer.py`, `test_case_pdf_xframe.py`, `test_historical_search.py`, `apps/accounts/tests/test_manual.py`

## 4. Requirements

- R1: `can_view_pdf = case.scheduling_decided_at is not None and case.status in
  PDF_ALLOWED_STATUSES` nos 4 pontos — cobre confirmação E negação de
  agendamento por **qualquer decisor** (equipe), em detalhe ativo e histórico.
- R2: **teste negativo explícito**: caso de negativa médica total em
  `FINAL_REPLY_POSTED`/`AWAITING_NIR_ACK` (`scheduling_decided_at IS NULL`) →
  SEM cards de PDF/texto e 404 em `scheduler:case_pdf`/`scheduler:pdf_viewer`.
- R3: caso reaberto por intercorrência (fora dos estados permitidos) continua 404;
  `xframe` e headers de serve inalterados.
- R4: manual (seção Agendador) atualizado — «processados hoje» da equipe +
  PDF/texto por decisão de agendamento; notificações descritas com precisão
  (seguem o criador).

## 5. Expected blast radius

- `apps/scheduler/views.py` (4 pontos do gate + helper se fizer sentido),
  `templates/scheduler/` (se o gate condicionar render de cards),
  `templates/accounts/manual.html`, testes listados.
- **Não muda**: `PDF_ALLOWED_STATUSES` (conjunto), `case_reopen`, detalhe do
  médico/NIR, workers, notificações (Non-Goal).
- Comportamento anterior que permanece: negativa médica sem PDF, reabertura sem
  PDF, referência humana, paridade serve×viewer.

## 6. Failing-before plan

Failing-before evidence:
- targeted test invertido (`test_detail_cards.py`/`test_pdf_viewer.py`):
  caso **confirmado por outro agendador** → espera cards visíveis e PDF servido
  ao agendador diferente; hoje 404/sem cards (`scheduled_by_id == user.pk`).
- targeted test (novo): negativa médica total em `FINAL_REPLY_POSTED` → espera
  SEM cards e 404 nas rotas (prova que a guarda de decisão é necessária).

## 7. Implementation constraints

- A guarda de decisão é obrigatória — `status in PDF_ALLOWED_STATUSES` sozinho é
  a brecha apontada no review (P1-1): negativa médica alcança esses estados sem
  passar pelo agendamento.
- Usar `scheduling_decided_at is not None` (ou `scheduling_decided_by_id`), não
  `scheduled_by` (negação de agendamento não grava `scheduled_by`).
- Sem mudar o conjunto `PDF_ALLOWED_STATUSES`.

## 8. Validation

**Focused validation** — `uv run pytest apps/scheduler/tests/test_detail_cards.py
apps/scheduler/tests/test_pdf_viewer.py apps/scheduler/tests/test_case_pdf_xframe.py
apps/scheduler/tests/test_historical_search.py apps/accounts/tests/test_manual.py`

**Project-required gates** — `AGENTS.md` §2 ao concluir (último slice: rodar o
gate completo da suíte).

## 9. Acceptance criteria

- [ ] R1: gate por decisão+estado nos 4 pontos; confirmação E negação de agendamento (testes positivos)
- [ ] R2: negativa médica sem cards/404 (teste negativo explícito)
- [ ] R3: reabertura 404; xframe/headers preservados (testes)
- [ ] R4: manual agendador finalizado (teste do manual)
- [ ] Failing-before no relatório; gate §2 completo executado

## 10. Escalation conditions

`BLOCKED_NEEDS_DECISION` se: algum dos 4 pontos não puder adotar a guarda sem
mudar comportamento aprovado; `historical_case_detail` revelar dependência não
mapeada; contradição spec×código.

## 11. Evidence report

`/tmp/hmd-slice-vis-007-report.md` (mesmo formato do slice 001).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-vis-007-report.md`.
