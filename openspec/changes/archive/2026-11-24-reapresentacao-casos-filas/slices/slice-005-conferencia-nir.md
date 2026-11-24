# Slice 005 — Conferência NIR de suspeitas e integração final

## 1. Identity
Change: `reapresentacao-casos-filas`. Task: [5.1](../tasks.md#5-s5--conferencia-nir). Depende de S4 aceito.

## 2. Objective
NIR da equipe compara suspeitas e responde mesmo pedido ou independente, aplicando com segurança os três fluxos já entregues sem triagem clínica.

## 3. Read first
- `AGENTS.md`, `PROJECT_CONTEXT.md`, `openspec/config.yaml`.
- `../proposal.md`, `../design.md` D1–D3 e D6–D8, Migration Plan.
- Todos os deltas em `../specs/`, em especial case-reappearance conferência e llm-pipeline confirmação tardia.
- S1–S4; `apps/cases/reappearance.py`, task/revisão/vínculo criados; `apps/intake/{views,services,forms,urls}.py`.
- `templates/intake/{case_detail,closed_case_detail,_my_cases_content}.html`, rotas NIR de documentos/viewer.
- `apps/pipeline/prior_case.py`, `apps/doctor/presenters.py`, `apps/cases/event_trail.py`, testes das superfícies anteriores.
- Helpers existentes de `_assert_nir_role`/confirmação de procedimento para padrão de re-check, não para misturar autoridades.
Paths ../ relativos a slices; código à raiz.

## 4. Requirements
- R1: origem fraca, idade forte divergente, CNS conflitante e ambiguidade aparecem como candidatos; duas respostas EXATAS, dados comparativos e PDFs acessíveis. Sem terceiro botão; GET computa candidatos sob demanda sem inserir CaseReappearance/eventos. Evidência: HTML/assert de ações, consultas capturadas sem INSERT/UPDATE/DELETE e integrações NIR equipe + documentos sintéticos.
- R2: independente suprime somente mesmo par/revisão; fechar sem responder mantém pipeline e filas operacionais. Evidência: navegação sem POST e repetição, alteração de fonte/declaração reabre conferência sem aceitar resposta antiga.
- R3: mesmo pedido confirma autoria/revisão e invoca serviço comum, não lógica em view: original médico só troca com novo pronto, original scheduler mantém-se, negativa informa card/LLM conforme momento da confirmação. Evidência: três jornadas HTTP/serviço; prior_context confirmado coexiste com resolução de fila sem IntegrityError; decisão negada e candidato ativo não se excluem; origem canônica nir_confirmed no evento/presenter; confirmação após sumarização mostra origem tardia sem reanálise ou decisão alterada.
- R4: CSRF, papel ativo+vínculo real rechecado sob transação, escolha única/par exato/revisão atual; obsoleto 400 e conflito transitório 409/pending sem sucesso falso. Evidência: POST direto malicioso, getlist, papel removido antes do serviço, retry/falha de dispatch e múltiplos candidatos sem encerramento indiscriminado.
- R5: integração completa documentada e comprovada com só dois botões, ausência de triagem clínica, três fluxos e rollback flag preservando acesso. Evidência: manual NIR atualizado, verify-hmd e regressões focadas de todos os slices.

## 5. Expected blast radius
Intake views/forms/services/urls/templates/testes; cases resolução/revisão/testes e trilha se eventos de confirmação ainda faltarem; prior_case/presenter para incluir confirmação vigente/tardia; manual NIR; seed/drive verify para jornada final. Sem nova persistência além de D2, salvo expansão justificada e escalada quando material.
Sensíveis: gate de formato/revisão de procedimento não muda; autorização de documentos S3 e UI S4 não afrouxam; policy, prompts, decisões e agendamento não ganham triagem clínica. Sem notificações novas, e-mails ou API.

## 6. Failing-before plan
RED: GET do detalhe novo com nome+origem exige comparação e exatamente duas ações (ausentes antes). RED POST como NIR não-criador não resolve antes da implementação. RED negativo de confirmação stale prova ausência de validação adequada. Fazer RED individual antes de cada comportamento, sem implementar primeiro e escrever teste depois.

## 7. Implementation constraints
Follow the repository engineering policy in `AGENTS.md` and preserve existing conventions. Prefer the smallest correct change; avoid speculative abstraction and unrelated refactoring.
Reusar resolução de S2/S3. Ausência de resposta não é estado de decisão. Comparação não concede aprovação clínica nem autoridade sobre procedimento. Para múltiplos candidatos, confirmação escolhe explicitamente um par; aplicação revalida demais vínculos e impede segundo original aplicado, sem encerrar todos os candidatos. Não declarar LLM informado se confirmação ocorreu após chamada. Não editar tasks durante execução.

## 8. Validation
**Focused:** intake novas confirmações/gates/PDF, cases resolução/concorrência, pipeline prior_case/llm2, doctor card, scheduler contador/documentos, closure/purga; teste de flag off com vínculo anterior.
**Project-required gates:** AGENTS.md e `openspec validate reapresentacao-casos-filas --strict` ao concluir implementação, sem marcar tasks por conta do worker.
**Runtime:** verify-hmd com login real NIR não-criador 1280/390; abrir ambos PDFs/Voltar; fechar sem responder; independente; confirmar cada uma das três situações. Mostrar que não há pergunta de atualização/revisão médica e agenda continua. Ler skill verify-hmd antes de alterar harness. Evidência sintética sem PHI real.

## 9. Acceptance criteria
- [ ] R1 compara corretamente e oferece só duas respostas, GET read-only.
- [ ] R2 silêncio/independente/revisão funcionam sem bloquear ou encerrar.
- [ ] R3 três fluxos usam serviços comuns e contexto tardio é honesto.
- [ ] R4 negativos de segurança/concorrência sem efeitos parciais.
- [ ] R5 manual, jornadas e regressões/gates verdes; nenhuma implementação de revisão clínica.

## 10. Escalation conditions
Parar BLOCKED_NEEDS_DECISION por contradição, decisão nova de produto/arquitetura, expansão material, mudança pública, schema/FSM não previsto, segurança/privacidade/integridade/clínica ou impossibilidade de evidência. Especialmente não criar terceiro botão, bloqueio por silêncio ou reavaliação médica defensiva.

## 11. Evidence report
`/tmp/sirhosp-slice-005-report.md`: READY_FOR_REVIEW/BLOCKED_NEEDS_DECISION; R→evidência, arquivos alterados, RED/GREEN, comandos/exits, runtime, expansão/justificativa, riscos e decisões humanas.

## 12. Worker handoff
Implement only this approved slice using listed artifacts, not conversation. Follow repository policy. Produce failing-before evidence, minimal implementation, focused/project/runtime validation and report. Do not update tasks.md, commit, push, merge, archive or start another slice. New decision: BLOCKED_NEEDS_DECISION; otherwise READY_FOR_REVIEW and REPORT_PATH=/tmp/sirhosp-slice-005-report.md.
