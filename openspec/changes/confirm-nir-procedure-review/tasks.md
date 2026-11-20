# Tasks

Executar um slice por vez; não implantar commits intermediários. Contratos em `slices/` são parte do planejamento, não autorização para implementar. Todas as tarefas estão pendentes. Cada slice exige RED → GREEN → REFACTOR e o quality gate de `AGENTS.md`; relatórios ficam em `/tmp/`, não no Git.

## 1. Slice 001 — Confirmação autoritativa até resumo e análise

Contrato: [slice-001-confirmation-to-analysis.md](slices/slice-001-confirmation-to-analysis.md). Design D2–D6; marcador D1 suportado em fixtures explícitas, sem ativar criação real antes do slice 002.

- [x] 1.1 Demonstrar RED de confirmação válida mantendo/trocando procedimento e repetindo a mesma divergência, via serviço real até worker com LLM fake; registrar falha comportamental anterior no relatório do slice 001.
- [x] 1.2 Implementar validação de autoridade/fonte/texto anonimizado/mapa, operação FSM, atomicidade durável, limpeza de derivados e serviço de confirmação; verificar R1–R3 com testes tipados de rollback, concorrência, autorização e preservação dos arquivos, distinguindo POST recusado sem mutação, integridade inválida no worker → FAILED sem dispatch e revisão/declaração obsoleta íntegra → gate automático, sem revalidar confirmação que já tenha evento INVALIDATED mesmo se os valores forem restaurados.
- [x] 1.3 Integrar trigger pós-commit, nova LLM1, ausência de novo gate sob confirmação vigente, contexto técnico LLM2 e análise exata do confirmado; verificar R4–R5 capturando chamadas e provando zero invenção/PHI, zero PDF/anonymization reexecutados e `APPLIED.automatic_procedures` igual à detecção real da nova passada (não união/conflitos), preservando a revisão original referenciada.
- [x] 1.4 Proteger conclusão contra resumo vazio/espaços ou análise residual/incompleta, mantendo gates de encerramento; verificar R6 com testes negativos sem evento `AWAITING_DOCTOR`, casos FAILED e no-op em reexecução, incluindo CLEANED com minimização verificado antes de digests/INVALIDATED/FAILED.
- [x] 1.5 Documentar contratos públicos nos módulos alterados, mapear rótulos dos eventos introduzidos e executar validação focada e gate do projeto; verificar R7 com regressões históricas verdes e relatório `/tmp/sirhosp-slice-001-report.md`.

## 2. Slice 002 — NIR confirma novos envios pela interface

Contrato: [slice-002-nir-review-ui.md](slices/slice-002-nir-review-ui.md). Depende de 001. Design D1–D2/D9.

- [x] 2.1 Demonstrar RED da jornada HTTP de novo envio → divergência → formulário → confirmação, incluindo manutenção e troca; verificar falha anterior de form/rota sem tomar 404 isolado como prova suficiente do comportamento final.
- [x] 2.2 Marcar novos casos na primitiva de criação, abrangendo lote/reenvio corrigido, e rejeitar bypass simples nos marcados; verificar R1–R2 sem alterar originais históricos, formato SESAB ou casos sem marcador.
- [x] 2.3 Entregar form, rota e detalhe com select único dos 13 tipos, justificativa, checkbox e snapshots de revisão/fonte; verificar R3–R4 com POST real, fallback sem JS, 500 caracteres, multiplicidade, escaping e reexibição sem consentimento automático.
- [x] 2.4 Entregar o adaptador LLM determinístico com override exclusivo de verify e fixtures pela opção `seed_verify --procedure-review`, que chama `seed_prompts` antes de criar casos; provar guardas/determinismo/zero rede, LlmError em falha/entrada desconhecida e idempotência dos números 990101–990104 antes/depois de confirmação sem reset de progresso/prompts. Demonstrar POST real manter/trocar no harness com pipeline real, atualizar manual NIR e verificar R5 em desktop/mobile com regressões; executar gate e produzir `/tmp/sirhosp-slice-002-report.md`.

## 3. Slice 003 — Confirmação durável com recuperação de dispatch

Contrato: [slice-003-dispatch-recovery.md](slices/slice-003-dispatch-recovery.md). Depende de 002. Design D7.

- [ ] 3.1 Demonstrar RED HTTP com `transaction=True`, injetando falha de enqueue no callback on_commit real; exigir confirmação durável, Schedule e auditoria corretos depois da resposta, não apenas mensagem de erro ou callback manualmente simulado.
- [ ] 3.2 Integrar enqueue seguro, Schedule ONCE após 60 s no cluster `llm`, deduplicação e `ProcedureReviewEnqueueAfterCommitError` propagada pelo callback `robust=False` à view; verificar R1–R2 com banco real, transação própria de recovery/auditoria commitada antes da exceção e parâmetros serializados executáveis.
- [ ] 3.3 Exibir outcomes programado (redirect 302 com aviso) e suporte (detalhe HTTP 503), registrando falha auditável sem PHI; verificar R3 com falhas de Schedule e da própria transação de recovery/auditoria, sem sucesso falso, reversão da confirmação, botão novo ou log de justificativa.
- [ ] 3.4 Provar execução de recuperação, job atrasado, rollback e ausência de loop/duplicação de efeitos; atualizar manual de suporte/recovery, verificar R4–R5, executar gate e produzir `/tmp/sirhosp-slice-003-report.md`.

## 4. Slice 004 — Médico vê a contradição e decide sobre a análise confirmada

Contrato: [slice-004-doctor-evidence.md](slices/slice-004-doctor-evidence.md). Depende de 003. Design D6/D8/D9 e integração do change.

- [ ] 4.1 Demonstrar RED do aviso médico em detalhe e decisão após confirmação real, e de POST de decisão sobre caso marcado inconsistente; registrar ausência/incorreção do comportamento antes de implementar.
- [ ] 4.2 Projetar aviso por eventos correlacionados, com mantido/trocado, declaração anterior, detecções, autor/data/justificativa e limite clínico; tornar auditoria legível; verificar R1–R2 com detecção original e nova diferentes (nova vem de APPLIED, original da revisão referenciada), HTML escapado e ausência de aviso fictício em casos automáticos/históricos.
- [ ] 4.3 Aplicar defesa de completude no trabalho médico e provar reroteamento por subtipo, singleton na decisão e liberdade clínica; verificar R3–R4 com GET/POST diretos, reserva válida e regressões de decisão/scheduling sem mudar regras clínicas.
- [ ] 4.4 Atualizar manual médico e provar jornadas de manutenção, troca para não detectado, detecção vazia e falha até sua ausência na fila médica, com testes integrados e `verify-hmd`; reutilizar o adaptador/fixtures D9 de 002, sem semear confirmação/APPLIED/artefatos finais, e verificar R5 com POST real, artefatos sintéticos e zero chamadas externas.
- [ ] 4.5 Executar integração final dos quatro slices, gate completo e check Django conforme `AGENTS.md`, validar este change com `openspec validate confirm-nir-procedure-review --strict`, registrar smoke do scheduler real (ou bloqueio operacional explícito) e produzir `/tmp/sirhosp-slice-004-report.md`; não arquivar nem atualizar contexto de archive nesta tarefa.
