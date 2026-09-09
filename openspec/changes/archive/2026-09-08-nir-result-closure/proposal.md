# Proposal: nir-result-closure

## Problema

O ciclo do caso **trava após a resposta final**: casos ficam para sempre em
`FINAL_REPLY_POSTED` (nada dispara `nir_acknowledge`/`start_cleaning`/
`complete_cleaning`); casos **negados pelo médico** nunca saem de
`DOCTOR_DENIED` (`post_final_reply` dessa origem é responsabilidade deste
change — plano §4: "DOCTOR_DENIED → FINAL_REPLY_POSTED direto"); não existe
limpeza dos dados clínicos após o encerramento (PDFs originais, texto
extraído, mapa de pseudônimos e artefatos LLM permanecem para sempre); o NIR
não tem visão de "casos encerrados" nem o **reenvio corrigido** herdado do
ats-web (novo caso vinculado a um encerrado).

## Objetivo

Fechar o ciclo do caso (plano §4 "[NIR U1] Confirma recebimento → CLEANING →
CLEANED"; roadmap 09 "Resultado final, confirmar recebimento → CLEANED,
casos encerrados, reenvio corrigido (herdado)"):

1. **Resposta final de negativa médica**: ao negar todos os procedimentos, a
   decisão publica automaticamente a resposta final ao NIR (motivos por
   procedimento) e o caso vai a `FINAL_REPLY_POSTED` — sem fila de
   agendamento.
2. **Ciência do NIR + limpeza transacional**: o criador do caso confirma o
   recebimento (`nir_acknowledge → start_cleaning → limpeza →
   complete_cleaning` no mesmo atomic, síncrono) — o caso chega a `CLEANED`
   com **minimização de dados**: documentos originais (rows+arquivos),
   `extracted_text`, `anonymized_text`, `pseudonym_map` e artefatos LLM
   (`structured_data`/`summary_text`/`suggested_action`/`policy_result`)
   removidos; **preservados** identificação, decisões médicas (prior-case e
   auditoria), trilha de eventos, comunicações e dados de agendamento.
3. **Resultado e casos encerrados para o NIR**: seção de resultado no detalhe
   do caso (decisões + agendamento + resposta final), botão de ciência e aba
   de casos encerrados em "meus casos" — tudo com escopo por criador.
4. **Reenvio corrigido** (semântica ats-web `create_corrected_resubmission`
   adaptada): de um caso **encerrado próprio**, o NIR cria um **novo caso**
   (pipeline completo desde `NEW`) vinculado por `corrects_case`, com motivo
   obrigatório e **tipos declarados explícitos (nunca herdados)**; o original
   só ganha o evento de supersedição.

## Escopo

**Inclui**: `apps/cases/closure.py` (serviços de fechamento); wiring da view
de decisão do médico (change 07); UI/URLs do intake (resultado/ciência/aba
encerrados/reenvio); migration única `cases/0009` (campos de correção);
eventos canônicos novos (`CASE_CORRECTION_CREATED`,
`CASE_MARKED_SUPERSEDED`); spec nova `case-closure`.

**Não inclui**: dashboard/notificações/PWA (11); OCR de anexos (10); limpeza
assíncrona/cluster de cleaning (síncrono por design — D6); mudanças no
`resubmit_case_documents` do change 04 (caso retido no gate é OUTRO fluxo —
D5); `scope_gate_bypass`/reprocessamento de tipo (não existe no HMD).

## Sucesso

- Médico nega todos → NIR vê a resposta final com motivos na thread e o caso
  em `FINAL_REPLY_POSTED`.
- NIR confirma recebimento → caso `CLEANED`, sem documentos/arquivos/texto/
  mapa/artefatos LLM, mantendo identificação + decisões + trilha; um caso
  limpo dentro da janela **continua elegível como prior-case**.
- "Meus casos" tem aba de encerrados; o reenvio corrigido cria novo caso
  vinculado, e o original permanece intacto (exceto evento).

## Capabilities

- `case-closure` (nova) — spec delta em `specs/case-closure/spec.md`.
