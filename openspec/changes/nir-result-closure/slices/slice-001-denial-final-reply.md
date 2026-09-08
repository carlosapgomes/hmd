# Slice 001: Resposta final de negativa médica

## Objetivo

Quando o médico nega **todos** os procedimentos, o caso sai de `DOCTOR_DENIED`
automaticamente: novo serviço `post_doctor_denial_reply` publica a resposta
final ao NIR (motivo por procedimento) na thread e leva o caso a
`FINAL_REPLY_POSTED` — wired na view de decisão do change 07.

## Contexto necessário

- `apps/cases/models.py` — op pública `post_final_reply(*, user, role)`
  (source inclui `DOCTOR_DENIED`; padrão `_run_transition`); `CaseProcedure`
  (`doctor_disposition`, `doctor_reason`, `procedure_type` — rows negadas
  têm `doctor_decided_at` preenchido).
- `apps/cases/communications.py::post_user_communication(case, *, user, role,
  body)` — user message na thread (padrão do scheduler no change 08).
- `apps/cases/procedures.py::record_doctor_procedure_decisions` — serviço do
  change 03 (NÃO modificar); termina em `DOCTOR_DENIED` quando todos negados.
- `apps/doctor/views.py::case_decide` (rota `doctor:case_decide`) — view do
  change 07 que chama o serviço acima; é onde o wiring entra.
- Design D1 (`openspec/changes/nir-result-closure/design.md`) — template como
  constante do módulo (`DENIAL_REPLY_TEMPLATE`); dados reais no texto (NIR é
  o remetente, plano §6).

## Requisitos verificáveis

- **R1** `apps/cases/closure.py` (novo módulo) com
  `post_doctor_denial_reply(case, *, user, role)`: valida estado ==
  `DOCTOR_DENIED` (senão `ValueError` nomeado, sem efeito) e existência de
  rows negadas; no MESMO atomic (`select_for_update`): `post_final_reply`
  (com `user`/`role` do médico) + `post_user_communication` com
  `DENIAL_REPLY_TEMPLATE` interpolada — cada procedimento negado com tipo
  (label legível do catálogo) e motivo.
- **R2** Sem rows negadas (estado certo mas dados inconsistentes) → erro
  nomeado sem efeito.
- **R3** Wiring: `apps/doctor/views.py::case_decide` chama o serviço do
  change 03 **e ignora seu retorno (ele devolve `None` e trabalha sobre um
  `locked` re-buscado — o `case` em memória fica stale)**; em seguida
  **re-lê o caso** (`case.refresh_from_db()`) e invoca
  `post_doctor_denial_reply` **somente se `status == DOCTOR_DENIED`**
  (decisão parcial → `SCHEDULER_REQUESTED` segue o fluxo existente, sem
  fechamento); `ValueError` do fechamento na view → `messages.error` +
  redirect (nunca 500; caso permanece `DOCTOR_DENIED`).
- **R4** Testes: negativa total via serviço (estado `FINAL_REPLY_POSTED`,
  evento `CASE_STATUS_FINAL_REPLY_POSTED`, comunicação com tipo+motivo de
  cada negado, autor=médico/papel doctor); estado errado recusado; wiring na
  view (POST negando tudo → thread contém a resposta; POST parcial →
  SCHEDULER_REQUESTED sem resposta final; erro do fechamento simulado via
  `monkeypatch` → mensagem + redirect, sem 500 — **obrigatório**).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1/R2 | `apps/cases/closure.py` | `test_denial_reply_publishes_motives`, `test_denial_reply_wrong_state_rejected`, `test_denial_reply_no_rows_rejected` |
| R3 | `apps/doctor/views.py` (case_decide) | `test_decide_all_denied_posts_final_reply`, `test_decide_partial_no_final_reply`, `test_decide_closure_error_no_500` |
| R4 | `apps/cases/tests/test_closure_denial.py`, `apps/doctor/tests/test_decision.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/closure.py                  # novo módulo (docstring pt-BR)
  - apps/cases/tests/test_closure_denial.py
  - apps/doctor/views.py                   # wiring mínimo em case_decide
  - apps/doctor/tests/test_decision.py     # +testes de wiring

out_of_scope:
  - acknowledge/limpeza (slice 002); UI do NIR (slice 003)
  - modificar record_doctor_procedure_decisions ou a FSM
  - system notices (SYSTEM_COMMUNICATION_TEXTS intocado)
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/cases/tests/test_closure_denial.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.cases.closure'`
  (ou `ImportError: cannot import name 'post_doctor_denial_reply'`).

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/cases/tests/ apps/doctor/tests/` —
  exit 0 (regressão: decisão do 07 e FSM do 03).
- `uv run ruff check apps/cases apps/doctor && uv run ruff format --check apps/cases apps/doctor`
- `uv run mypy .`

## Critérios de aceitação

- [ ] R1–R4 comprovados; resposta contém **cada** procedimento negado com
      tipo legível e motivo real
- [ ] Transição + comunicação no MESMO atomic
- [ ] Decisão parcial inalterada (nenhum call ao fechamento)
- [ ] Gate parcial do slice verde
