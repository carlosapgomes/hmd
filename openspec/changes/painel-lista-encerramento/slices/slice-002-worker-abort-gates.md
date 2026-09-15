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
- `apps/pipeline/policy.py`: dentro do atomic, re-ler e retornar `{}`
  sem persistir quando `CLEANED` (retorno tipado `dict`, chamador ignora).
- `apps/pipeline/llm2_service.py`: idem, retornando o **sentinel tipado**
  `Llm2SummarizationResult(suggestions={}, aggregate="",
  aggregate_reasons=[], retry_used=False)` — `{}` não passa no mypy.
- `apps/pipeline/llm1_service.py` (~330): re-lê DENTRO do atomic de
  persistência; se `CLEANED`, pula TODAS as escritas (`structured_data`,
  `manual_review_*`, eventos) e devolve o resultado computado sem
  persistir.
- `apps/pipeline/prior_case.py`: idem, `None` (contrato `-> None`).
- `apps/pipeline/orchestrator.py`: após cada `refresh_from_db` pré-passo,
  retornar quando `CLEANED` (evita `TransitionNotAllowed` em
  `start_/complete_llm_summarization`); **refresh+gate logo APÓS
  `run_llm1_extraction`, ANTES do ramo de divergência e de
  `complete_llm_extraction`** (o save full da transição ressuscitaria
  status+campos+lock de uma linha CLEANED e envenenaria os gates
  seguintes); no `except`, idem.

### R2 — Testes (com INTERCALAÇÃO — sem ela o gate de topo no-opa e o teste
fica verde sem fix; seam SEMPRE fora do atomic de escrita, senão o re-read
do fix roda antes da intercalação e o teste nunca fica verde; lease já
EXPIRADA no banco ANTES de intercalar, senão a recusa de lease viva aborta
a intercalação em vez do write)

- intake: caso com documentos pendentes → task entra no passo (seam:
  `evaluate_regulation_report`/`_extract_document_text`, ~206-216) →
  INTERCALA `administratively_close_case` → a task prossegue o passo →
  nada gravado (sem `extracted_text`, sem evento de retenção, sem
  ressurreição de status).
- anonymization: idem (seam imediatamente antes do atomic ~160, APÓS
  `start_anonymization` — seam em ~144 nunca fica verde: o save full da
  self-transition ressuscitaria a linha CLEANED antes do gate) →
  `anonymized_text`/`pseudonym_map`/status/lock seguem limpos após o
  passo retornar.
- pipeline llm1: seam no stub do cliente LLM (padrão dos testes com
  clientes fake) que INTERCALA o encerramento durante a chamada →
  `structured_data` segue zerado, nenhum evento LLM1 gravado, status
  permanece `CLEANED` (orquestrador aborta antes da divergência/
  `complete_llm_extraction`).
- pipeline policy/llm2: caso encerrado → `evaluate_case_policies`
  retorna `{}` e o passo de llm2 retorna o sentinel tipado, sem
  persistir; `prior_case` idem (`None`).
- Handlers de erro: caso CLEANED no `except` → sem `fail_processing`, sem
  exceção propagada (uma task representativa).

## Out of Scope

- Rota/UI/painel (slice 003); gate no worker de anexos (não precisa).

## Expected files

- apps/intake/tasks.py
- apps/anonymization/tasks.py
- apps/pipeline/orchestrator.py
- apps/pipeline/policy.py
- apps/pipeline/llm1_service.py
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

- Seam de anonymization via WRAP do gate `_is_administratively_closed`
  (intercala o closure e delega à implementação real — o re-read de produção
  roda): não há colaborador patchável genuíno entre o porteiro de texto
  vazio e o atomic; seam em `start_anonymization` é rejeitado por design.
- RED do worker provado por neutralização `if False and …` (short-circuita a
  chamada); a review apontou infidelidade no teste de anonymization —
  hardening do parent fecha com duas camadas extras (abaixo) e o teste pina
  a INVARIANTE final (nada persistido), não um gate único: neutralizar só o
  gate externo é capturado pelo gate interno serializado; neutralizar ambos
  cai na proteção estrutural (o refresh do gate muta a instância a CLEANED →
  `complete_anonymization` levanta `TransitionNotAllowed` → rollback do
  atomic) — defence-in-depth de 3 camadas.
- Hardening do parent (review P1): teste irmão do sub-caminho de RETENÇÃO
  (`_OFF_PATTERN_LINES`) — o único onde o gate é a única proteção (o atomic
  de retenção commita via `return False`); discriminante: RED provado com
  `git stash` do `apps/intake/tasks.py` (teste falha sem o gate).
- Hardening do parent (review P2): gate serializado `select_for_update` no
  topo do atomic de anonymization (fecha a janela entre o gate externo e o
  save full durante a anonimização em si) e gate CLEANED no atomic final do
  orquestrador antes de `complete_llm_summarization` (abort benigno, sem
  `logger.exception` de erro).
- Gates de erro do anonymization/orquestrador cobertos por "uma task
  representativa" (R2 permite); except do intake pinnado.
