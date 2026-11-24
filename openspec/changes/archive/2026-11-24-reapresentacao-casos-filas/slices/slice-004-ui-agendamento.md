# Slice 004 — Reapresentações visíveis na fila e histórico do agendador

## 1. Identity
Change: `reapresentacao-casos-filas`. Task: [4.1](../tasks.md#4-s4--ui-do-agendador). Depende de S3 aceito. Segunda metade do antigo S3; não modifica a resolução nem a política de purga.

## 2. Objective
Agendador encontra contador, documentos recebidos e espera herdada no fluxo de trabalho e histórico, sem confundir nova documentação com relatório aprovado.

## 3. Read first
- `AGENTS.md`, `PROJECT_CONTEXT.md`, `openspec/config.yaml`.
- `../proposal.md`, `../design.md` D4–D5 e D8, Migration Plan.
- `../specs/scheduling/spec.md`, `../specs/pdf-original-viewer/spec.md`; contrato S3 e suas rotas autorizadas.
- `apps/scheduler/{views,presenters,urls}.py`, `templates/scheduler/{case_detail,_queue_content}.html`.
- `apps/cases/{models,reappearance}.py`, helpers de espera introduzidos por S2.
- `apps/scheduler/tests/test_views.py`, testes de PDF/viewer existentes, `templates/accounts/manual.html`.
- Harness verify-hmd e seed existente; ler skill verify-hmd antes de alterá-lo.
Referências ../ relativas a slices; código relativo à raiz.

## 4. Requirements
- R1: card/partial mostram número de incoming distintos aplicados, último recebimento e link à seção. Evidência: três incoming + retry de um; consultas anotadas/prefetch sem N+1, paginação/filtros/polling preservados.
- R2: `scheduler:case_detail` é a página de trabalho ÚNICA contendo formulários POST de confirmar/negar. Seção separa envios com data, responsável, regulação, relatório e anexos PDF/imagem. Evidência: HTTP autenticado e browser, sem GETs novos de confirmação/negação; abrir arquivos/Voltar preserva acesso sem alterar reserva/estado, sem alegar aprovação médica dos recebidos.
- R3: histórico canônico CLEANED retido mantém seção e acesso a recebidos; minimizado/purgado informa indisponibilidade sem links quebrados prometendo arquivo. PDF original mantém gate antigo. Evidência: HTTP/browser em retenção/minimização, feature off com vínculos existentes e negativo original CLEANED.
- R4: espera herdada da substituição médica chega ao agendamento: card/partial exibem marco herdado, FIFO usa marco após days_on_screen novo; criação real intacta. Evidência: caso médico substituído aprovado segue até fila scheduler com tempo congelado e assert de HTML/ordem, inclusive idade zero/days null sem erro. Não mudar aba Processados Hoje para exibir espera pendente.
- R5: manual e jornada real mostram contador, relatórios/anexos, histórico e possibilidade de agendar sem pergunta clínica/suspensão. Evidência: verify-hmd 1280/390 com login real, PDF.js/Voltar seguro, desktop lazy por requisição, PDF/imagem/anexo PDF e confirmação apesar do reenvio.

## 5. Expected blast radius
Scheduler views/presenters/templates/testes; reuso dos helpers de espera/resolução de S2/S3 sem redesenho; manual agendador/NIR; seed/drive verify e mapa da feature. Não criar migration, transição, retry, política de retenção ou novo guard. As rotas autorizadas já existem em S3. Justificar expansão legítima no relatório e escalar mudança material.

## 6. Failing-before plan
RED HTTP do card/partial e detalhe com vínculo real S3 exige contador/seção ainda ausentes. RED card/FIFO com criação nova e marco herdado antigo demonstra perda da espera. Registrar failing-before visual usando a mesma fixture sintética antes de implementar UI; não precisa recriar falhas de backend já cobertas em S3.

## 7. Implementation constraints
Follow the repository engineering policy in `AGENTS.md` and preserve existing conventions. Prefer the smallest correct change; avoid speculative abstraction and unrelated refactoring.
Sem alterações da resolução/purga, sem copiar novos PDFs para a lista do relatório aprovado. Reusar lazy/viewer/next seguro e IDs únicos de collapse por envio/arquivo. Não introduzir GET em case_confirm/case_deny. Documentos novos são recebidos, não aprovados. Feature off não apaga nem esconde vínculos aplicados. Nenhuma revisão clínica nova.

## 8. Validation
**Focused:** scheduler fila/partial/detalhe/histórico, query count em múltiplos cards, rota recebida versus original, espera herdada médica→scheduler, manual.
**Project-required gates:** AGENTS.md.
**Runtime/verification evidence:** verify-hmd 1280/390 com arquivos sintéticos; matriz de navegação contador→seção→PDF/anexo→Voltar, lazy sem fetch antecipado e histórico CLEANED retido; agendamento real segue possível. Usar mídia isolada por run e guardas do harness existente.

## 9. Acceptance criteria
- [ ] R1 contador/último envio/links/partial sem duplicação ou N+1.
- [ ] R2 documentos recebidos separados na página de trabalho real, sem páginas extras.
- [ ] R3 histórico retido acessível e originais/minimizados protegidos, inclusive flag off.
- [ ] R4 card/partial/FIFO conservam espera herdada sem alterar criação/dias oficiais.
- [ ] R5 manual e evidências 1280/390 entregues, sem gate clínico; quality gate verde.

## 10. Escalation conditions
Parar BLOCKED_NEEDS_DECISION por contradição, nova decisão de produto/arquitetura, expansão material, mudança pública, schema/FSM não previsto, segurança/privacidade/integridade/clínica ou impossibilidade de prova. Não compensar erro de autorização modificando guard do PDF original.

## 11. Evidence report
`/tmp/sirhosp-slice-004-report.md`: READY_FOR_REVIEW/BLOCKED_NEEDS_DECISION; R→evidência, arquivos, failing-before/passing-after, comandos/exits, runtime/queries, expansão justificada, riscos/limitações e decisões humanas.

## 12. Worker handoff
Implement only this approved slice using listed artifacts, not conversation. Follow repository engineering policy. Produce failing-before evidence, minimal implementation, focused/project/runtime validation and report. Do not update tasks.md, commit, push, merge, archive or start another slice. New decision: BLOCKED_NEEDS_DECISION; otherwise READY_FOR_REVIEW and REPORT_PATH=/tmp/sirhosp-slice-004-report.md.
