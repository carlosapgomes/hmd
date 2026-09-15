# Slice 002 — Abort de escrita pós-encerramento (workers)

## Contexto necessário

- A janela de corrida é criada pelo slice 001: antes dele, nenhum caso ia a
  `CLEANED` fora de `FINAL_REPLY_POSTED`. Worker travado num passo longo
  (extração de N PDFs, anonimização, LLM) com lease EXPIRADA (default 300s,
  `apps/cases/locks.py:79-83`) retorna DEPOIS do encerramento
  administrativo e pode repovoar dados clínicos zerados pela minimização.
- Gate de topo das tasks JÁ existe e aborta em CLEANED — os fixes daqui são
  os gates DENTRO dos atomics de escrita (a janela real).
- `apps/intake/tasks.py`: `_extract_and_decide` lê documentos FORA do
  atomic (~214-215) e grava DENTRO com linha relida (~218-229) sem checar
  status; o caminho de RETENÇÃO (~241-253: `manual_review_required` +
  evento + release que cai em `CaseLockConflictError` → `return False`)
  COMMITA o atomic — o gate precisa vir antes de QUALQUER write, inclusive
  o evento de retenção; handlers gravam `CaseEvent` (~232-247).
- `apps/anonymization/tasks.py`: após refresh+gate de topo (~134-148),
  chama `anonymize_case_text(case)` com a instância e
  `case.complete_anonymization(...)` (~160-162); o serviço faz
  `case.save()` FULL (`apps/anonymization/services.py:189-198`) que
  ressuscitaria `status`, `anonymized_text`, `pseudonym_map`,
  `patient_name`, `patient_birth_date` e os 6 campos de lock lidos antes do
  claim.
- Pipeline: atomics de escrita clínica sem checagem em
  `apps/pipeline/policy.py` (~679-690, `policy_result`; retorno tipado
  `-> dict[...], policy.py:652), `apps/pipeline/llm2_service.py` (~428-439,
  `summary_text`/`suggested_action`), `apps/pipeline/prior_case.py`
  (~218-246); chamadores com `refresh_from_db` pré-passo em
  `apps/pipeline/orchestrator.py` (~132/157/163) e `except` chamando
  `fail_processing` (só aceita os 4 estados de processamento).
- Worker de anexos NÃO precisa de gate (só escreve na row do anexo; re-lê e
  devolve None se a row sumiu — `apps/attachments/tasks.py:11-13`).

## Goal

Nenhum worker em voo persiste dados clínicos, eventos ou estado em caso já
encerrado administrativamente (CLEANED); falhas inócuas, sem explosão.

## Deliverables

### R1 — Gates in-atomic

- `apps/intake/tasks.py`: dentro do atomic de `_extract_and_decide`,
  re-ler o caso ANTES de qualquer write; se `CLEANED`, abortar (sem gravar
  `extracted_text`, sem evento de retenção, sem `manual_review_required`).
  No `except`, re-lê e NÃO chama `fail_processing` quando `CLEANED`.
- `apps/anonymization/tasks.py`: re-ler ANTES de
  `anonymize_case_text`/`complete_anonymization`; se `CLEANED`, abortar
  (o save full do serviço não roda). No `except`, idem.
- `apps/pipeline/policy.py` / `llm2_service.py` / `prior_case.py`: dentro
  do atomic, re-ler e retornar `{}` (policy/llm2 — orquestrador ignora o
  retorno; mypy satisfeito) ou `None` (prior_case, contrato existente)
  sem persistir quando `CLEANED`.
- `apps/pipeline/orchestrator.py`: após cada `refresh_from_db` pré-passo,
  retornar quando `CLEANED` (evita `TransitionNotAllowed` em
  `start_/complete_llm_summarization`); no `except`, idem.

### R2 — Testes (com INTERCALAÇÃO — sem ela o gate de topo no-opa e o
teste fica verde sem fix)

- intake: caso com documentos pendentes → task entra no passo →
  INTERCALA `administratively_close_case` (lease expirada) → a task
  prossegue o passo → nada gravado (sem `extracted_text`, sem evento de
  retenção, sem ressurreição de status), atomic sem commit de writes.
- anonymization: idem → `anonymized_text`/`pseudonym_map`/status/lock
  seguem limpos após o passo retornar.
- pipeline: caso encerrado → `evaluate_case_policies` e o passo de llm2
  rodam → retornam `{}` sem persistir; `prior_case` idem; orquestrador
  aborta sem `TransitionNotAllowed` e sem evento de falha.
- Handlers de erro: caso CLEANED no `except` → sem `fail_processing`, sem
  exceção propagada (uma task representativa).

## Out of Scope

- Rota/UI/painel (slice 003); gate no worker de anexos (não precisa).

## Expected files

- apps/intake/tasks.py
- apps/anonymization/tasks.py
- apps/pipeline/orchestrator.py
- apps/pipeline/policy.py
- apps/pipeline/llm2_service.py
- apps/pipeline/prior_case.py
- apps/cases/tests/test_closure_worker_abort.py (novo — ou nomes coerentes
  com os testes existentes de cada app; preferir UM módulo novo que importe
  os helpers de fixture existentes)
- allowed incidental: testes existentes das tasks/pipeline se o gate exigir
  ajuste de fixture (declarar no Deviations)

## Verification (RED → GREEN, mesmo comando)

```bash
TEST_DB_PORT=55435 uv run pytest apps/cases apps/intake apps/anonymization apps/pipeline -q
```

## Acceptance criteria

- Testes de intercalação verdes (intake, anonymization, pipeline ×2,
  handler de erro); campos clínicos/lock/status seguem zerados; suíte
  completa verde; ruff/format/mypy.

## Deviations / learnings

- (preenchido na execução)
