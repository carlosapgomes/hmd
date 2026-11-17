# ADR-0010: Retenção temporária dos dados clínicos no encerramento (fase de estabilização)

## Status

Accepted

## Contexto

O HMD está em fase de estabilização, em produção com pacientes reais
desde 2026-09-13, e o encerramento do caso (ciência do NIR e encerramento
administrativo do painel) minimizava incondicionalmente os dados
clínicos: os 7 campos clínicos/LLM eram zerados e as rows e os arquivos
físicos de documentos e anexos eram removidos. Essa disposição imediata
impedia dois trabalhos da fase: a **auditoria supervisora da assistência**
— rever, sobre o material real do caso encerrado, o que o médico viu e
decidiu — e o **benchmark de produto com corpus real** (calibração do
pipeline, anonimização e prompts sobre o material efetivamente
processado). Sem o material retido, a auditoria só teria resíduo
administrativo e o benchmark só corpus sintético.

## Decisão

- **Modo de retenção configurável por ambiente** (flag
  `CASE_CLOSURE_MINIMIZATION_ENABLED` em `config/settings/base.py`,
  default `false` = retenção): com a retenção ativa, o encerramento
  (ciência e administrativo) vai a `CLEANED` com FSM, eventos, locks e
  notificações idênticos, mas **nada é zerado nem deletado** — os 7
  campos clínicos/LLM, as rows e os arquivos físicos de documentos e
  anexos permanecem intactos e acessíveis pelas rotas dos papéis
  autorizados. Com `true`, a minimização original segue byte-a-byte.
  A função interna de limpeza (`_clean_acknowledged_clinical_data`)
  permanece incondicional — a decisão de modo mora nos dois entry
  points — para que a purga retroativa futura reuse a mesma semântica
  minimizante.
- **Base jurídica (resposta do dono, 2026-11-17)**: execução íntegra e
  exclusiva dentro do perímetro do hospital, com **autorização expressa
  dos gestores**, cobrindo as duas finalidades — auditoria supervisora
  da assistência e benchmark de produto com corpus real. Nada do corpus
  retido sai do perímetro do hospital.
- **Escopo**: apenas os 7 campos clínicos/LLM (`extracted_text`,
  `anonymized_text`, `pseudonym_map`, `structured_data`, `summary_text`,
  `suggested_action`, `policy_result`) e as rows/arquivos de documentos
  e anexos. Identificação do paciente, decisões médicas, trilha de
  eventos, comunicações, `anonymization_report` e dados de agendamento
  nunca foram tocados pela minimização e seguem intocados pela retenção
  — nada além desse conjunto entra na política temporária.
- **Worker de anexos com gate explícito de caso encerrado**: a retenção
  remove a proteção colateral da minimização (a deleção da row), então
  `process_case_attachments` aborta em `CLEANED` antes de cada etapa,
  inclusive antes do dispatch de OCR externo.
- **Textos e trilha verdadeiros nos dois modos**: as telas de ciência,
  encerramento administrativo e manual usam texto neutro ("disposição
  dos dados conforme a política vigente — ADR-0010") e o rótulo de
  `CASE_STATUS_CLEANED` é "Caso concluído".

## Alternativas Consideradas

1. **Minimização imediata mantida** — rejeitada: inviabilizaria a
   auditoria supervisora e o benchmark com corpus real na fase de
   estabilização, que são exatamente o trabalho pendente.
2. **Retenção permanente com TTL automático** — rejeitada: não há
   política de retenção decidida; a purga é operacional (comando),
   executada quando o dono decidir.
3. **Gate dentro da função interna de limpeza** — rejeitada: tornaria a
   purga retroativa um no-op silencioso sob o default `false`.
4. **Snapshot histórico congelado no encerramento** — rejeitada: os
   artefatos persistidos no caso são os apresentados ao médico (a FSM
   nunca reentra no pipeline pós-decisão); nada de novo precisa ser
   congelado.

## Consequências

- **PHI real retida no piloto** (`pseudonym_map` incluso) — aceite
  transitório da fase de estabilização, com fundamento e perímetro
  registrados nesta decisão; o volume do piloto é pequeno e o custo de
  storage é monitorável por contagem no `--dry-run` da purga.
- **Reversão operacional completa** (runbook): 1) ligar
  `CASE_CLOSURE_MINIMIZATION_ENABLED=true` no ambiente do serviço `web`
  (passthrough no `docker-compose.prod.yml`) — a nova disposição passa
  a minimizar; 2) rodar a purga retroativa com `--dry-run`
  (`manage.py purge_closed_case_data --dry-run`) e revisar o relatório;
  3) executar a purga real; 4) registrar a execução (data, escopo,
  contagens, responsável) no runbook deste ADR. A reversão nunca
  depende de mudança de código: a minimização original permanece no
  código e nas specs como modo suportado.
- **Gatilho de reversão**: o produto alcançar a **versão 1.0**.
- **Responsável** pela reversão e pelo procedimento de acesso ao corpus
  retido: **gerência de TI do hospital**. Todo acesso ao corpus retido
  (auditoria, benchmark, purga) ocorre dentro do perímetro do hospital
  e sob aprovação da gerência de TI.
- **Casos já `CLEANED` antes desta decisão** permanecem minimizados:
  a purga retroativa nunca precisa tocá-los (não têm dados retidos).
