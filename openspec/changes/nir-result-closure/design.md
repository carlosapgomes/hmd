# Design: nir-result-closure

Referências: plano §4 (fecho da FSM: "[NIR U1] Confirma recebimento →
CLEANING → CLEANED"; "DOCTOR_DENIED → FINAL_REPLY_POSTED direto"), §10
roadmap 09; FSM do change 03 (ops públicas `post_final_reply`, 
`nir_acknowledge`, `start_cleaning`, `complete_cleaning` — todas com `*, user,
role`; `post_final_reply` já aceita `DOCTOR_DENIED` como source);
`apps/pipeline/prior_case.py` (dependências do prior-case); padrões dos
changes 04/07/08 (escopo por criador, erros nomeados, atomic+
`select_for_update`, mensagens+redirect); ats-web `create_corrected_resubmission`
(somente-leitura — semântica do reenvio corrigido).

## D1 — `apps/cases/closure.py`: serviços de fechamento do ciclo

Novo módulo de serviços do núcleo (não do app de um papel): o fechamento é
ciclo de vida do caso, consumido por views do doctor (negativa) e do intake
(ciência). Padrão transacional dos services existentes (atomic +
`select_for_update`, validações nomeadas `ValueError` **antes** de qualquer
escrita, transições via ops públicas):

- **`post_doctor_denial_reply(case, *, user, role)`** (slice 001): exige
  `DOCTOR_DENIED`; compõe a resposta das rows negadas
  (`CaseProcedure.doctor_disposition=denied` com `doctor_reason` — texto
  real: o NIR é o remetente e vê dados reais, plano §6); no mesmo atomic:
  `post_final_reply` + `post_user_communication` (autor = médico, papel
  `doctor`, template constante `DENIAL_REPLY_TEMPLATE` interpolada com
  tipo+motivo por procedimento). Quem chama: a view `doctor:case_decide`
  (change 07) imediatamente após `record_doctor_procedure_decisions` retornar
  negativa total — a decisão parcial (≥1 aprovado) segue para o agendador e
  **não** toca no fechamento. Falha entre os dois atomics deixa o caso em
  `DOCTOR_DENIED` (re-chamável pela mesma view em nova submissão? Não — a
  view decide uma vez; caso raro de falha intermediária fica visível no
  estado e é operável via admin; documentado como risco residual aceito).
- **`acknowledge_case_receipt(case, *, user, role)`** (slice 002): exige
  `FINAL_REPLY_POSTED` e `case.created_by == user` (escopo por criador —
  erro nomeado/`Http404` na view, sem vazar informação); no MESMO atomic
  encadeia `nir_acknowledge` → `start_cleaning` → **limpeza (D2)** →
  `complete_cleaning` (3 eventos `CASE_STATUS_*`; `AWAITING_NIR_ACK` e
  `CLEANING` são transitórios — nunca descansam).

## D2 — Conteúdo da limpeza (minimização) e ordem de deleção

**Remove** (no atomic): rows `CaseDocument` (`case.documents.all().delete()`);
zera `extracted_text`, `anonymized_text`, `pseudonym_map` ({}),
`structured_data`, `summary_text`, `suggested_action`, `policy_result`.
**Preserva**: identificação (`patient_name`/`patient_birth_date`/
`agency_record_number` — prior-case 15d + lista de encerrados), rows
`CaseProcedure` com decisões/motivos (auditoria + prior-case 7d —
`lookup_prior_case_context` lê apenas `CaseProcedure` + identificação,
**não** lê artefatos do caso), trilha `CaseEvent`, thread de comunicações,
campos `scheduled_*` (auditoria operacional).

**Arquivos físicos**: coletar os nomes ANTES do `delete()` das rows; dentro
do atomic só o banco; a deleção física best-effort roda em
`transaction.on_commit` (log em falha; orfão eventual é resíduo aceito — o
rollback não reverte filesystem, por isso a ordem rows→commit→arquivos;
espelha a lógica compensatória do change 04 invertida para deleção).
`intake:serve_document` de caso limpo → 404 (rows não existem mais).

## D3 — Resultado e casos encerrados para o NIR

`intake:case_detail` (change 04) ganha **seção de resultado** quando o caso
passou da decisão: decisões por procedimento (label+disposição+motivo — dados
reais: NIR é o remetente), dados de agendamento (unidade/data/local ou
negativa do agendador) e a resposta final em destaque na thread (última
comunicação do fechamento/agendamento). **Botão "Confirmar recebimento"**
somente em `FINAL_REPLY_POSTED` + criador (`POST intake:case_ack` →
`acknowledge_case_receipt`; erros → mensagem+redirect, nunca 500; não-criador
→ 404). `intake:my_cases` ganha abas **ativos** (default: tudo exceto
`CLEANED`) e **encerrados** (`CLEANED`, mesmos items/badges, criador).

## D4 — Reenvio corrigido (novo caso vinculado)

Migration única `cases/0009_case_correction`: `corrects_case` (self-FK
`SET_NULL`, null, `related_name="corrected_by"`), `correction_reason`
(`TextField` blank), `correction_created_by` (FK User `SET_NULL`, null).
Eventos canônicos novos em `apps/cases/events.py` (aditivo):
`CASE_CORRECTION_CREATED` (no novo, payload com id do original + motivo) e
`CASE_MARKED_SUPERSEDED` (no original).

**`create_corrected_resubmission(*, original_case, user, role, files,
procedure_types, correction_reason)`** em `apps/intake/services.py`:
valida motivo não vazio, `original_case.status == CLEANED`, criador
(`_assert_owned_by`), lote (`_validate_batch`) e tipos
(`_validate_declared_types`) — **tipos são escolha explícita do NIR, nunca
herdados do original** (semântica ats-web R3/F1); no atomic, cria o novo caso
reutilizando `create_case_with_documents` **com kwargs aditivos opcionais**
(`corrects_case`, `correction_reason`, `correction_created_by` — default
`None`/`""` mantém o comportamento do change 04; o enqueue do worker pdf já
acontece lá) + posta `CASE_MARKED_SUPERSEDED` no original (ator NIR) e
`CASE_CORRECTION_CREATED` no novo. O original NÃO muda de status nem perde
dados. UI: botão "Reenviar corrigido" no detalhe de caso `CLEANED` próprio →
formulário (arquivos + tipos + motivo) → redirect ao detalhe do novo caso; o
detalhe do original lista `corrected_by` (ordenado por criação).

## D5 — Fronteira com o `resubmit_case_documents` do change 04

O reenvio do change 04 atua sobre o **mesmo caso retido no gate** (documentos
fora do padrão, caso ainda em processamento) — substitui documentos e
reprocessa. O reenvio corrigido do 09 atua sobre **caso encerrado** e cria um
**novo caso** vinculado (novo pipeline completo). Sem sobreposição de
estados; ambos ficam disponíveis na UI em contextos distintos.

## D6 — Limpeza síncrona, sem cluster novo

O plano não define cluster de cleaning (pdf/anonymization/llm apenas) e a
limpeza é leve (deletes+zeragem). Os estados `AWAITING_NIR_ACK`/`CLEANING`
existem na FSM para a trilha e futura variação assíncrona, mas o 09 executa
o fecho **síncronamente** na request do ack (transições encadeadas no mesmo
atomic — padrão já usado pelo scheduler no 08). Nenhum signal/on_commit além
da deleção física de arquivos (D2).
