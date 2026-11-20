# Slice 003 — Confirmação durável com recuperação de dispatch

## Identity
- Change: `confirm-nir-procedure-review`.
- Tasks: `../tasks.md`, grupo 3 (3.1–3.4).
- Depende de 002 aceito.

## Objective
Quando o dispatch falhar após confirmar, preservar a confirmação e oferecer recuperação programada ou erro operacional explícito, sem encaminhar o relatório puro.

## Read first
- `AGENTS.md`, `PROJECT_CONTEXT.md`, `openspec/config.yaml`.
- `../proposal.md`; `../design.md`, D7 e riscos de scheduler; delta `../specs/llm-pipeline/spec.md`, requisito de recuperação.
- Contratos/resultados aceitos 001/002; `apps/pipeline/{tasks,signals,orchestrator}.py`.
- `apps/intake/{services,views}.py` e teste HTTP da confirmação.
- `config/settings/base.py` (Q_CLUSTER), `docker-compose.prod.yml` (cluster `llm`), `pyproject.toml` (django-q2 pinado).
- `apps/cases/events.py`, `apps/dashboard/event_labels.py`, `templates/accounts/manual.html`.
- Referência somente-leitura: `/projects/dev/ats-web/apps/intake/services.py`, `_enqueue_pipeline_or_schedule_recovery`; **não** copiar o destino PDF da referência.

## Requirements
| ID | Contrato | Evidência planejada |
|---|---|---|
| R1 | Exceção de enqueue pós-commit não reverte confirmação/declarado/fonte; callback real agenda ONCE em 60 s no cluster llm e commita auditoria em transação própria antes de propagar erro tipado à view. | Novo `apps/pipeline/tests/test_procedure_review_recovery.py`: POST HTTP `transaction=True`, callback sem simulação, confirmação/Schedule/evento consultados depois da resposta. |
| R2 | Enqueue tem um único dono; callback de rollback não dispara; recovery pendente é deduplicado e UUID serializado executa corretamente. | Contagem de chamadas, schedules, callbacks e teste invocando destino persistido com argumentos serializados. |
| R3 | Callback levanta `ProcedureReviewEnqueueAfterCommitError` com caso, confirmação, `recovery_scheduled` e código; view trata programado como redirect 302 com aviso e suporte como detalhe HTTP 503, sem PHI/botão retry. Falha da transação de recovery/auditoria nunca reverte a confirmação ou anuncia Schedule não commitado. | HTTP real de ambos outcomes e falha de auditoria, consulta evento `…ENQUEUE_FAILED` quando persistido, canário/caplog e HTML sem sucesso falso. |
| R4 | Recovery realmente roda pipeline sem PDF; fora de estado/encerrado/concluído não duplica efeitos ou declaração antiga; erro de LLM permanece FAILED sem loop. | Integração worker real + LLM fake, jobs atrasados, assertions de eventos e dispatch, zero chamadas PDF/anonimização. |
| R5 | Operação e documentação identificam cluster/scheduler e limite da recuperação. | Manual atualizado e smoke qcluster isolado ou bloqueio operacional registrado com passos de reprodução. |

## Expected blast radius
- `apps/pipeline/{tasks,signals,orchestrator}.py`; novo teste `test_procedure_review_recovery.py` e testes de signals estritamente afetados.
- `apps/intake/views.py` para captura explícita da exceção tipada pós-commit; serviço mantém o atomic durável de 001 e a propagação do callback, sem transportar retorno do signal nem duplicar enqueue.
- `apps/cases/events.py`, `apps/dashboard/event_labels.py` para evento de falha; testes de rótulos e HTTP.
- `templates/accounts/manual.html` na orientação de falha/suporte.
- Sem novo modelo/migration, scheduler alternativo, settings/env/dependência, rota de retry, transição de FAILED, retry de LLM ou mudança do worker PDF. Expansão de arquivos exige justificativa; nova arquitetura exige escalada.

## Failing-before plan
1. Em `transaction=True`, cliente NIR submete confirmação válida; injetar erro no dispatch, não no serviço de confirmação. RED deve mostrar ausência de recovery/outcome correto, não ausência da confirmação.
2. Segundo teste também faz falhar a gravação de Schedule; exige confirmação durável, auditoria commitada e detalhe HTTP 503 para suporte. Terceiro teste falha a transação inteira de recovery/auditoria: a confirmação continua, Schedule não é anunciado e log sanitizado não alega auditoria gravada.
3. `uv run pytest apps/pipeline/tests/test_procedure_review_recovery.py`; capturar RED e depois GREEN. Simular erro no broker mantendo banco real de teste; Schedule não deve ser um mock sem inspeção dos parâmetros persistidos.

## Implementation constraints
- Seguir `AGENTS.md` e padrão pós-commit; não tratar falha de dispatch como falha de processamento com rollback tardio.
- `Schedule.ONCE`, cluster explícito `llm`, alvo `apps.pipeline.orchestrator.process_case_pipeline`, atraso 60 s, nome caso+confirmação e nenhuma dependência do cluster default não implantado.
- Callback único `on_commit(..., robust=False)` no signal do pedido FSM; não duplicar pelo serviço/view nem tentar retornar outcome pelo signal. O commit durável acontece antes da exceção tipada, e a view só captura essa exceção específica.
- `ProcedureReviewEnqueueAfterCommitError` é definida em `apps/pipeline/tasks.py` com `case_id`, `confirmation_event_id`, `recovery_scheduled` e `reason_code`. Recovery e evento de falha usam outra transação; tentar Schedule em savepoint para poder auditar sua falha e commitar antes de levantar. Se o atomic inteiro falhar, reportar `recovery_scheduled=False` com log técnico sanitizado, sem rollback da confirmação nem alegação de auditoria persistida.
- Programado = redirect 302 com aviso; sem recovery = detalhe HTTP 503 com orientação de suporte, sem formulário de repetição. Diferenciar exceção do dispatch de erro de LLM inline já tratado pelo orquestrador.
- Recuperação pode repetir chamada externa se não houve persistência; exigir idempotência de efeitos, não prometer exactly-once de rede.
- Job antigo nunca reescreve seleção; estado/fonte/revisão/autoridade continuam sendo revalidados.
- Gravar falha com código enxuto e outcome; nunca exceção livre/payload clínico/justificativa em logs/evento de erro.

## Validation
**Focused:** `uv run pytest apps/pipeline/tests/test_procedure_review_recovery.py apps/pipeline/tests/test_signals.py apps/intake/tests/test_procedure_review_ui.py apps/dashboard/tests/test_event_labels.py` e testes de pipeline da confirmação.

**Project-required gates:** quality gate e check Django de `AGENTS.md`.

**Runtime/verification:** smoke com qcluster nomeado `llm`, broker/banco/media isolados e cliente LLM fake controlado pelo ambiente de teste: Schedule deve ser consumido e produzir a cadeia nova com artefatos; observar que nenhum worker PDF é necessário. Ler contrato do harness antes de usar `verify-hmd`; não rodar workers sobre dados dev/prod. Se não existir runtime seguro, documentar bloqueio e comando/protocolo exato a executar antes da liberação; unitário de Schedule não conta como smoke real.

## Acceptance criteria
- [ ] R1: confirmação durável, ONCE correto e auditoria commitada antes da exceção do callback comprovados por HTTP com commit real.
- [ ] R2: um trigger, rollback zero dispatch e deduplicação/UUID corretos comprovados.
- [ ] R3: canal da exceção, outcomes 302-aviso/503-suporte, falha da transação de recovery/auditoria e ausência de PHI/botão demonstrados.
- [ ] R4: execução/atraso/falha de LLM respeitam estados e não duplicam efeitos.
- [ ] R5: documentação e evidência operacional, ou bloqueio explícito pré-release, presentes; gate verde.

## Escalation conditions
`BLOCKED_NEEDS_DECISION` se django-q2 instalado não conseguir consumir ONCE no cluster previsto sem mudança operacional/configuração material, se for necessário um outbox/modelo novo, se houver requisito de retry geral de FAILED ou outra decisão não prevista de disponibilidade/privacidade. Não substituir por cron, sleep ou loop silencioso.

## Evidence report
`/tmp/sirhosp-slice-003-report.md`: status, R1–R5 → evidências, arquivos, RED/GREEN, comandos/exit status, dados não sensíveis do Schedule/worker, bloqueios runtime, desvios e riscos residuais. Não versionar relatório.

## Worker handoff
Implementar apenas este slice, com RED → GREEN → REFACTOR, menor mudança, validação e relatório. Não atualizar `tasks.md`, commitar, push, merge, arquivar ou iniciar outro slice. Finalizar `READY_FOR_REVIEW` com `REPORT_PATH=/tmp/sirhosp-slice-003-report.md`; decisão nova implica parar `BLOCKED_NEEDS_DECISION` com evidência.
