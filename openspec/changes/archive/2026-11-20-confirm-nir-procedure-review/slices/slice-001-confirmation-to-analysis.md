# Slice 001 — Confirmação autoritativa até resumo e análise

## Identity
- Change: `confirm-nir-procedure-review`.
- Tasks: `../tasks.md`, grupo 1 (1.1–1.5).
- Dependências: nenhuma. Não implantar este commit isoladamente.

## Objective
Entregar a capacidade de confirmar manutenção/troca pelo serviço e reprocessar o mesmo caso até resumo/análise, sem novo loop pela divergência e sem fabricação de evidência.

## Read first
- `AGENTS.md`, `PROJECT_CONTEXT.md`, `openspec/config.yaml`.
- `../proposal.md`; `../design.md`, D1–D6 e riscos; deltas `../specs/{case-management,intake-nir,llm-pipeline}/spec.md`.
- `apps/cases/{models,events,procedures,locks}.py`.
- `apps/intake/services.py`, especialmente criação e `release_retained_case`.
- `apps/pipeline/{llm1_service,llm2_service,orchestrator,signals,tasks}.py`, `schemas/llm2.py`.
- `apps/pipeline/tests/{test_orchestrator,test_llm1,test_llm2,test_signals}.py`, `apps/intake/tests/test_gate_divergence.py`.
- Referência somente-leitura: `/projects/dev/ats-web/apps/cases/procedure_review.py` e `apps/pipeline/tests/test_human_confirmed_procedures.py`.

## Requirements
| ID | Contrato | Evidência planejada |
|---|---|---|
| R1 | Serviço aceita singleton mantido/trocado com consentimento e justificativa; exige criador/papel NIR, marcador, revisão e fonte atuais, sem decisão/lease conflitante. | Novo `apps/intake/tests/test_procedure_review_confirmation.py`, testes diretos de serviço válidos/negativos. |
| R2 | Confirmação, declaração, limpeza dos quatro derivados, reset de detecção, operação FSM e release são atômicos; fontes/id/tempo não mudam. | Snapshots DB/storage antes/depois, erro injetado, transações reais e segunda submissão. |
| R3 | Autoridade exige evento mais recente e digests de fonte/texto anonimizado/mapa; integridade inválida no worker → FAILED sem novo dispatch, revisão/declaração alterada com integridade preservada → gate automático, nunca confirmação antiga ou já invalidada, mesmo após restaurar valores. POST obsoleto continua sem mutação. | Novo `apps/cases/tests/test_procedure_review.py` e integração pipeline: mesmos nomes/bytes distintos, texto/mapa alterados isoladamente, fonte indisponível, review superseded; falha de integridade prevalece mesmo sem divergência clínica. |
| R4 | Uma confirmação reexecuta LLM1 com perfil confirmado, policy/prior/LLM2, sem PDF/Presidio novos; mesma divergência não recria retenção, mas detecção real não vira consenso. | Novo `apps/pipeline/tests/test_human_confirmed_procedures.py`, worker e serviços reais com LLM fake capturável; contar chamadas/eventos/flags e exigir `APPLIED.automatic_procedures` igual à detecção nova ordenada (inclusive vazia), não à união/conflitos. |
| R5 | Sem item do confirmado, analisar só dados existentes, sem item/evidence inventados; justificativa/autor não vão a LLM; policy continua prevalecendo. | Artefato original e view capturados, canário identificador na justificativa, teste policy recusar + LLM aceitar. |
| R6 | Caso marcado não chega a `AWAITING_DOCTOR` sem resumo útil e análise completa da execução atual; falha vai a FAILED, CLEANED não ressuscita. | Guard público + testes do orquestrador: vazio/espaços, policy/sugestão ausente/antiga/outro conjunto, erro LLM e encerramento em voo com minimização; CLEANED deve abortar antes de gravar INVALIDATED/FAILED por digests removidos. |
| R7 | Contrato histórico e gate SESAB continuam; enum/rótulos novos completos e docstrings corretas. | Suítes antigas de gate/FSM/signals e `apps/dashboard/tests/test_event_labels.py`. |

## Expected blast radius
**Provável:**
- Novo `apps/cases/procedure_review.py`, `apps/cases/{events,models}.py`; `procedures.py` apenas se necessário ao reset de detecção sem apagar rows.
- `apps/intake/services.py` (novo serviço; não marcar criação real neste slice).
- `apps/pipeline/{llm1_service,llm2_service,orchestrator,signals}.py`, `schemas/llm2.py`.
- `apps/dashboard/event_labels.py` para tipos introduzidos; novos testes da matriz e ajustes estritos nas suítes listadas.
- `apps/pipeline/tasks.py` somente se preciso normalizar UUID; recuperação é slice 003.

**Sensível/fora:** novas rotas/forms/templates/criação prospectiva (002), recovery Schedule (003), aviso/render médico (004), critérios clínicos, settings, migrations, notificações e dependências. Não criar abstração de workflows genérica. Justificar arquivo adicional antes de editar; expansão material exige escalada.

## Failing-before plan
1. Criar fixture sintética com PDF real de teste, anonimização/mapa coerentes e **marcador explícito**; rodar a primeira LLM1 fake para gerar retenção real com snapshot da fonte.
2. Escrever testes de ratificar A contra B e trocar A para C ausente; usar o serviço público e disparo pós-commit real, não simular `APPLIED` nem o estado final.
3. Rodar `uv run pytest apps/intake/tests/test_procedure_review_confirmation.py apps/pipeline/tests/test_human_confirmed_procedures.py`; registrar RED. Falha inicial de símbolo é aceitável no primeiro RED, mas prova final precisa percorrer os serviços concretos.
4. Adicionar RED de resumo só de espaços e chamada pública de conclusão sem artefatos; não remover assert antigo para forçar sucesso.
5. Adicionar RED de fonte alterada, texto anonimizado ausente/divergente e mapa alterado (incluindo durante resposta LLM): worker termina FAILED, invalida uma vez, não persiste resultado em voo nem dispara etapa seguinte. Contraprova: só revisão/declaração mudou, digests íntegros → reconciliação automática, com e sem divergência. POST obsoleto retorna erro sem mudar estado.

6. Demonstrar que declaração alterada → INVALIDATED → declaração restaurada não revive a confirmação, não recupera confirmação antiga nem duplica evento ao revalidar (incluindo antes de LLM2). Testar nova detecção diferente da original e também vazia: APPLIED deve transportar exatamente a nova detecção, conservando a referência da revisão original.

## Implementation constraints
- Seguir política de engenharia do repositório em `AGENTS.md`; menor mudança correta, sem refactor alheio.
- O slice é vertical na API de serviço: teste de confirmação até o resultado médico, não uma coleção de helpers desconectados. Exposição pela UI virá em 002.
- FSM self-transition sem estados novos; confirmação verdadeira não usa bypass de `LLM_SUMMARIZING`.
- Validar confirmação em LLM1 **e** no orquestrador, distinguindo observação de bloqueio. Ausência de INVALIDATED correlacionado é critério obrigatório; invalidação é definitiva para aquele evento, conforme D3. Revalidar estado, fingerprint, digest do texto anonimizado e digest canônico do mapa antes dos dispatches e após chamadas longas; integridade inválida segue FAILED de D3, não o gate automático.
- Em cada revalidação pós-chamada longa, reler CLEANED antes de digests e antes de registrar INVALIDATED/FAILED; minimização de encerramento não gera invalidação espúria. `APPLIED.automatic_procedures` contém somente a detecção da nova passada na ordem canônica, e o original é reconstruído da revisão referenciada, nunca dessa lista nova.
- O atomic durável da confirmação é o commit real; não permitir atomic externo de produção atrasando o callback. Testes da integração usam `transaction=True`; GET/POST de validação rejeitado não executam o desfecho FAILED reservado ao worker.
- Nenhuma dependência de `cases` para pipeline/LLM; guard de completude estrutural no domínio, schemas no pipeline.
- Fixtures históricas sem marcador preservadas; novos testes marcados não podem pular completude.
- Disparo por um único signal `on_commit`, lease liberada antes; não chamar LLM no atomic da confirmação. Não antecipar recuperação/UX de erro do 003.

## Validation
**Focused:** comandos RED acima; depois `uv run pytest apps/cases/tests apps/intake/tests/test_procedure_review_confirmation.py apps/intake/tests/test_gate_divergence.py apps/pipeline/tests apps/dashboard/tests/test_event_labels.py`.

**Project-required gates:** quality gate e check Django de `AGENTS.md` antes de reportar.

**Runtime/evidence:** teste `transaction=True` com cadeia pós-commit real e LLM fake deve mostrar mesma instância, nova LLM1, resumo não vazio e análise exata; mocks podem cobrir cliente externo, não substituir o serviço/orquestrador. Sem navegador neste slice.

## Acceptance criteria
- [ ] R1: manter/trocar válidos e negativas de autorização/obsolescência provadas no serviço.
- [ ] R2: rollback, exclusividade e preservação integral das fontes comprovados.
- [ ] R3: digests, FAILED por integridade, gate automático apenas íntegro, POST obsoleto sem mutação e impossibilidade de reviver confirmação invalidada comprovados.
- [ ] R4: fluxo real chega à fila com nova extração, detecção nova exata em APPLIED/revisão original preservada e nenhuma retenção repetida.
- [ ] R5: ausência clínica permanece ausência e nenhum canário livre chega ao LLM.
- [ ] R6: nenhum evento de fila em falhas; CLEANED com minimização aborta sem INVALIDATED/FAILED espúrios.
- [ ] R7: regressões, rótulos e gate completos verdes.

## Escalation conditions
Retornar `BLOCKED_NEEDS_DECISION` diante de conflito spec/design, expansão material, nova decisão pública/clínica/privacidade, necessidade de migration/estado/dependência ou impossibilidade de segurança com as APIs de locks existentes. O desfecho de fonte/anonimização/mapa alterados já está definido em D3: FAILED operacional no worker; não é questão pendente. Qualquer alternativa ou novo recovery de FAILED exige escalada.

## Evidence report
`/tmp/sirhosp-slice-001-report.md`: status `READY_FOR_REVIEW` ou `BLOCKED_NEEDS_DECISION`; R1–R7 → evidência; arquivos; RED/GREEN; comandos e exit status; prova transacional; expansão de blast radius; riscos e decisões pendentes. Não versionar relatório nem duplicar diff em snippets.

## Worker handoff
Implementar somente este slice, reconstruindo contexto pelos arquivos listados. Produzir RED, menor implementação, REFACTOR, validações e relatório. Não atualizar `tasks.md`, commitar, push, merge, arquivar ou iniciar outro slice. Ao concluir: `READY_FOR_REVIEW` e `REPORT_PATH=/tmp/sirhosp-slice-001-report.md`; se houver decisão nova, parar como bloqueado com evidência.
