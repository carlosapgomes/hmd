# Slice 002 — Linha de crédito do médico nas respostas finais ao NIR

## Identity

- Slice: S2
- Change: `registro-profissional-resposta-final`
- Task: `tasks.md` → «S2 — Linha de crédito do médico nas respostas finais ao NIR»

## Objective

Compor, na postagem, a linha `Médico responsável: {nome} — {CRM N}` como
última linha do corpo das três respostas finais ao NIR (confirmação
unidade 1, confirmação unidade 2, negativa médica), com degradação graciosa,
e renderizar a thread multilinha (`linebreaksbr`).

## Read first

- `AGENTS.md`
- `openspec/changes/registro-profissional-resposta-final/proposal.md`
- `openspec/changes/registro-profissional-resposta-final/design.md` — D2–D5
- `openspec/changes/registro-profissional-resposta-final/specs/scheduling/spec.md`
- `openspec/changes/registro-profissional-resposta-final/specs/case-closure/spec.md`
- `apps/accounts/models.py` — `display_name`, `professional_registration_display`
- `apps/cases/models.py` — `Case.doctor_decided_by` (FK `SET_NULL`)
- `apps/scheduler/services.py` — `confirm_case_scheduling`,
  `REPLY_UNIT_1_TEMPLATE`, `reply_unit_2_text()`
- `apps/cases/closure.py` — `post_doctor_denial_reply`, `DENIAL_REPLY_TEMPLATE`
- `apps/cases/communications.py` — `post_user_communication` (contrato do post)
- `apps/cases/tests/test_closure_denial.py` — igualdade estrita da negativa
  (factory carimba decisor); atualizado pelo slice
- `templates/accounts/manual.html` — parágrafo das respostas finais (R8)
- Templates: `templates/intake/case_detail.html`,
  `templates/intake/closed_case_detail.html`,
  `templates/scheduler/case_detail.html` (render de `communication.body`)

## Requirements

- R1: Helper único em `apps/cases` compõe a linha a partir de
  `case.doctor_decided_by`: com registro →
  `Médico responsável: {display_name} — {professional_registration_display}`
  (com `.strip()` defensivo ao ler o registro formatado — campo texto livre
  preenchido à mão no admin); sem registro →
  `Médico responsável: {display_name}`; decisor nulo → `""`.
  Puro, sem escrita, sem tocar sessão.
- R2: `confirm_case_scheduling` (unidade 1) anexa a linha como última linha
  do corpo (`\n`-separada) ao texto de `REPLY_UNIT_1_TEMPLATE`.
- R3: `confirm_case_scheduling` (unidade 2) anexa a linha ao texto-base de
  `reply_unit_2_text()`; a função pública permanece SEM argumentos e com o
  texto-base inalterado (composição na postagem).
- R4: `post_doctor_denial_reply` anexa a linha após os itens negados
  (`\n`-separada), usando `case.doctor_decided_by` (mesmo helper).
- R5: Degradacoes SEM FALHA: médico sem registro → linha só com nome;
  `doctor_decided_by` nulo → linha omitida; a postagem e as transições
  acontecem normalmente em ambos os casos.
- R6: Mensagens FORA do escopo permanecem exatamente como hoje:
  `REPLY_DENY_TEMPLATE` (negação de agendamento) e
  `REPLY_REOPEN_TEMPLATE` (intercorrência) sem a linha.
- R7: `|linebreaksbr` aplicado a `communication.body` nos 3 templates da
  thread (NIR detalhe, NIR histórico, agendador) — a linha de crédito (e os
  itens da negativa pré-existentes) separam por linha no HTML.
- R8: O manual de usuário (`templates/accounts/manual.html`) passa a citar a
  linha do médico responsável no parágrafo das respostas finais por unidade
  (ajuste mínimo de texto; nenhum teste fixa o parágrafo — verificado).
- R9: Autoria/papéis/FSM/locks/atomics inalterados — a mensagem continua
  auturada por quem opera (agendador nas confirmações, médico na negativa).
- Evidência: testes de serviço (igualdade do corpo completo nas 3 respostas,
  com/sem registro) + teste negativo do decisor nulo + pins de não-regressão
  das mensagens fora de escopo + asserção de renderização (`<br` presente no
  HTML do detalhe com corpo multilinha).
- Mapa de testes existentes (auditado na revisão independente do plano):
  - QUEBRAM por igualdade estrita (atualizar para o corpo completo):
    `apps/cases/tests/test_closure_denial.py:123`,
    `apps/scheduler/tests/test_views.py:1478`;
  - PERMANECEM verdes pela degradação (casos SEM decisor — `_case_in_state`
    com `user=None`; NÃO alterar, são pins implícitos da omissão):
    `apps/scheduler/tests/test_services.py:199/245`,
    `apps/scheduler/tests/test_views.py:1475` (substring `in`);
  - PINS de não-regressão do escopo (NÃO alterar):
    `apps/scheduler/tests/test_services.py:381` (negação de agendamento — R6),
    `apps/scheduler/tests/test_incident.py`,
    `apps/scheduler/tests/test_unit_labels.py`.

## Expected blast radius

- Esperado: novo helper em `apps/cases` (ex.: `doctor_credit.py` ou função em
  `communications.py`); `apps/scheduler/services.py`;
  `apps/cases/closure.py`; 3 templates da thread;
  `templates/accounts/manual.html` (R8); atualização de
  `apps/cases/tests/test_closure_denial.py` e
  `apps/scheduler/tests/test_views.py` (linha da igualdade) + testes novos
  (helper, degradações, render) — ver mapa em Requirements.
- Sensível/intocável: FSM e transições; locks/reservas; guards de papel;
  `apps/cases/communications.py::post_user_communication` (contrato);
  anonimização/pipeline LLM; notificações (previews fixos permanecem);
  `apps/accounts/models.py`.
- Comportamento preservado: rótulo vigente da unidade 2 no momento da
  postagem; sem ponto final no texto-base da unidade 2; ordenação canônica
  dos itens da negativa; históricos já gravados NÃO são reescritos.
- Testes: SOMENTE os dois de igualdade estrita do mapa QUEBRAM
  (`test_closure_denial.py:123`, `test_views.py:1478`); os demais do mapa são
  pins (implícitos da degradação ou de escopo) e devem permanecer intocados —
  qualquer alteração além disso exige justificativa no relatório.

## Failing-before plan

Failing-before evidence:
- targeted tests (escritos primeiro):
  - helper: ausência total da função/comportamento
  - `uv run pytest apps/scheduler/tests/test_services.py -k "responsavel or responsavel_medico or credit" -x`
    (assert do corpo completo com a linha) — falha: corpo atual sem a linha
  - denial: assert do corpo com a linha em teste novo/expandido do closure
    (molde de `apps/cases/tests/test_closure_denial.py`) — falha idem
  - render: assert `<br` no HTML do detalhe — falha (sem linebreaksbr hoje)
- expected failure: linha `Médico responsável:` ausente dos corpos postados
  e `<br` ausente no HTML renderizado.

## Implementation constraints

- Helper único (D2): NÃO duplicar a lógica entre scheduler e closure; fonte
  única `case.doctor_decided_by` — nunca o `user` do post.
- `reply_unit_2_text()` mantém assinatura sem argumentos e texto-base
  inalterado (D3) — a composição acontece no serviço.
- Formato exato da linha (sem ponto final adicional): prefixo literal
  `Médico responsável: `, separador ` — ` (espaço, travessão, espaço)
  entre nome e registro.
- Não implementar S3/outros consumidores (perfil, cards) — fora de escopo.
- Não alterar previews de notificação nem títulos.

## Validation

Focused validation (worker):
- `uv run pytest apps/scheduler apps/doctor apps/intake apps/cases -x` (suítes tocadas)
- `uv run pytest apps/accounts -x` (não-regressão do S1)

Project-required gates: `AGENTS.md` §2 (gate completo obrigatório — suíte
inteira).

Runtime/verification evidence: não exigida (comportamento textual coberto
por testes de serviço/view; jornadas verify-hmd não fixam esses textos —
verificado no planejamento).

## Acceptance criteria

- [ ] R1 helper único com as 3 formas (com registro/sem registro/nulo)
- [ ] R2 corpo U1 termina com a linha
- [ ] R3 corpo U2 = texto-base + linha; `reply_unit_2_text()` intacta
- [ ] R4 corpo da negativa termina com a linha
- [ ] R5 degradações sem falha (testes negativos)
- [ ] R6 negação de agendamento e intercorrência sem a linha (pins)
- [ ] R7 `linebreaksbr` nos 3 templates com asserção de renderização
- [ ] R8 manual cita a linha do médico responsável
- [ ] R9 autorias/FSM inalterados
- [ ] Mapa de testes respeitado (2 atualizados, pins intocados)
- [ ] Failing-before evidence registrada
- [ ] Gate completo verde

## Escalation conditions

Pare e retorne `BLOCKED_NEEDS_DECISION` se: contradição spec×design×código;
necessidade de migration/mudança de modelo; exigência de reescrever
mensagens históricas; impacto em notificações além do previsto; qualquer
decisão de produto não coberta por D1–D7.

## Evidence report

`/tmp/hmd-slice-registro-002-report.md` — status
(`READY_FOR_REVIEW`/`BLOCKED_NEEDS_DECISION`), requirements→evidence,
arquivos alterados, failing-before, passing-after, comandos+exit,
expansões de blast radius justificadas, riscos.

## Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy in `AGENTS.md` and preserve existing
conventions. Prefer the smallest correct change; avoid speculative abstraction
and unrelated refactoring.
Produce failing-before evidence, implement the smallest correct change, run
focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
