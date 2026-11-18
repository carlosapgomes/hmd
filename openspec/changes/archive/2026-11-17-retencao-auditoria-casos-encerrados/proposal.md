# Proposal

## Why

O sistema está em fase de desenvolvimento/piloto e a minimização vigente no
encerramento (D2 do change 09: campos clínicos/LLM zerados, documentos e
anexos deletados) destrói exatamente o material de que o time precisa até o
produto estabilizar: corpus real para o benchmark de anonimização e de
modelos, e o conteúdo apresentado ao médico (sumário clínico + estrutura
extraída) para auditoria supervisora das decisões — inclusive de casos
encerrados administrativamente. Além disso, o detalhe do caso no painel
(manager/admin) não expõe hoje o resumo consultivo que embasou a decisão
médica, tampouco para casos já encerrados.

## What Changes

- **Retenção temporária no encerramento** (decisão do dono, não permanente):
  com o flag `CASE_CLOSURE_MINIMIZATION_ENABLED = false` (default durante a
  fase de estabilização), a ciência do NIR e o encerramento administrativo
  **preservam** tudo — os 7 campos clínicos/LLM (`extracted_text`,
  `anonymized_text`, `pseudonym_map`, `structured_data`, `summary_text`,
  `suggested_action`, `policy_result`), as rows e os arquivos físicos de
  `CaseDocument` e `CaseAttachment`. Estados, eventos, guards, locks e
  notificações do encerramento permanecem idênticos.
- **Reversão operacional**: o flag reativa a minimização original quando o
  produto estabilizar; um comando de gerência `purge_closed_case_data`
  minimiza retroativamente os casos encerrados acumulados na fase de
  retenção (mesma semântica do encerramento minimizante).
- **Card de auditoria no painel**: o detalhe do caso no painel
  (manager/admin) ganha card collapsible «Sumário clínico» + «Estrutura
  extraída» — o mesmo conteúdo re-identificado apresentado ao médico na
  avaliação — cobrindo casos ativos **e** encerrados (dados retidos).
- **ADR novo** documentando a política temporária de retenção: fundamento
  (fase de desenvolvimento — LGPD art. 11, II, tutela da saúde/legítimo
  interesse do controlador para benchmark e auditoria), escopo, gatilho de
  reversão e operacionalização (flag + purga).

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

- `case-closure`: os requirements «Ciência do NIR com limpeza de dados
  clínicos» e «Encerramento administrativo do caso» passam a minimizar
  condicionalmente ao flag `CASE_CLOSURE_MINIMIZATION_ENABLED` (retenção
  total enquanto false); documento de caso encerrado deixa de ser 404 com
  retenção ativa; novo requirement de purga retroativa dos dados retidos.
- `dashboard`: o detalhe do caso no painel ganha requirement de card com o
  sumário clínico e a estrutura extraída re-identificados (mesma fonte do
  presenter médico), exibidos também para casos encerrados na fase de
  retenção.

## Impact

- `apps/cases/closure.py`: decisão de modo de retenção nos dois entry
  points (a função interna de limpeza permanece incondicional — é a mesma
  que a purga reusa); novo serviço de purga retroativa.
- `config/settings/base.py` + `.env.example` + `docker-compose.prod.yml`:
  flag `CASE_CLOSURE_MINIMIZATION_ENABLED` (default `false`) no padrão dos
  flags existentes (`INTAKE_ENABLED`, `*_RUN_TASKS_INLINE`), com
  passthrough no serviço `web` do compose de produção.
- `apps/attachments/tasks.py`: worker de anexos ganha gate explícito
  `CLEANED → no-op` antes de cada etapa (inclusive o dispatch de OCR
  externo) — hoje sua única proteção era a deleção da row, que a retenção
  remove.
- Textos de UI e trilha que afirmam a minimização tornam-se condicionais
  ao modo ou neutros (`templates/intake/case_detail.html`,
  `templates/dashboard/admin_close_confirm.html`,
  `templates/dashboard/case_detail.html`, `templates/accounts/manual.html`,
  `apps/dashboard/event_labels.py` — rótulo de `CASE_STATUS_CLEANED`
  renomeado para "Caso concluído").
- `apps/dashboard/`: presenter do detalhe ganha sumário/estrutura
  re-identificados (builders extraídos para `apps/cases/presentation.py`
  com API pública única — `apps/doctor/presenters.py` passa a importar
  dele); `templates/dashboard/case_detail.html` ganha o card collapsible.
- Novo management command (em `apps/cases`) para a purga retroativa,
  reusando a semântica minimizante de `closure.py`.
- Testes: suítes que hoje **exigem** a minimização
  (`test_closure_ack.py`, `test_administrative_closure.py`,
  `test_closure_worker_abort.py`, serve de documentos por papel) passam a
  cobrir os dois modos com pinos explícitos; nova cobertura do card do
  painel (padrão `apps/dashboard/tests/test_detail_cards.py`) e da corrida
  worker de anexos × encerramento (padrão
  `apps/cases/tests/test_closure_worker_abort.py`).
- `docs/adr/`: ADR-0010 (política de retenção temporária — fundamento por
  finalidade, gatilho de reversão verificável e responsável são
  pendências do dono que bloqueiam o archive, não o código).
- Sem mudanças de FSM, rotas, guards de papel, schema de banco ou
  dependências.
