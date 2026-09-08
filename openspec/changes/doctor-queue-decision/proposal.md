# Proposal: doctor-queue-decision

## Problema

O change 06 entrega casos em `AWAITING_DOCTOR` com artefatos completos
(`structured_data`, `policy_result`, `summary_text`, `suggested_action`) — mas
não existe superfície para o médico: sem fila, sem visão re-identificada do
paciente (os artefatos vivem anonimizados em tokens), sem alertas da policy e
sem registro da decisão por procedimento. Os serviços de domínio da decisão já
existem desde o change 03 (`record_doctor_procedure_decisions` com encadeamento
FSM `DOCTOR_DENIED | DOCTOR_ACCEPTED → SCHEDULER_REQUESTED`), porém sem UI e
sem controle de acesso por subtipo de médico.

## Objetivo

Entregar o **estágio médico** completo (plano §10): app `apps/doctor` com fila
de casos por estado com filtro por subtipo (`angio|neuro|cardio|radio`),
access control por subtipo sobre o conjunto de procedimentos do caso,
presenter **re-identificado** (médico/admin veem dados reais; a barreira
"LLM só vê tokens" permanece intacta — a re-identificação acontece só na
renderização para papel autorizado) e decisão **por procedimento** com motivo
obrigatório em negativas, gravando trilha auditável.

## Escopo

**Inclui**

- `User.specialties`: model `DoctorSpecialty` code-first semeado do catálogo
  (`VALID_DOCTOR_SUBTYPES`) + M2M em User (vazio = generalista) + migration +
  admin.
- `apps/doctor`: fila (estado `AWAITING_DOCTOR` + filtro por subtipo +
  "decididos"), presenter re-identificado (identificação, histórico/sumário
  LLM2, alertas da policy + recomendação por procedimento, agregado,
  prior-case com motivo real, artefato estruturado re-identificado, PDF
  original), formulário de decisão por procedimento e view de caso decidido.
- Helper recursivo de re-identificação (aditivo em
  `apps/anonymization/reidentify.py`) para estruturas aninhadas
  (`structured_data`/`policy_result`/`suggested_action`).
- Nav por papel ativo (`doctor`/`admin`) no `base.html`.

**Não inclui (out)**

- Fila/validação de agendamento e `await_scheduling_confirmation` (change 08).
- `post_final_reply`/resposta ao NIR (change 09 — caso negado permanece em
  `DOCTOR_DENIED` após este change).
- Anexos com OCR e cards de verificação de paciente (change 10).
- Dashboard/notificações (change 11); manager sem fila (perfil de gestão).
- Edição self-service de specialties (atribuição via Django admin).
- Lock de posse do médico por caso (divergência deliberada vs ats-web — ver
  design D4).

## Sucesso

- Médico com subtipo vê e decide apenas casos cujo conjunto declarado inclua
  ao menos um tipo do seu subtipo; generalista (sem subtipo) e admin veem
  tudo; outros papéis recebem 403.
- A decisão por procedimento persiste rows + eventos + transição FSM no mesmo
  atomic (serviço do change 03) e nenhum valor real de paciente é serializado
  para LLM em nenhum ponto (nenhuma chamada LLM neste change).
- Presenter mostra dados reais apenas sob papel ativo `doctor`/`admin`.

## Capabilities

- `doctor-decision` (nova) — spec delta em `specs/doctor-decision/spec.md`.
