# Proposal: scheduler-multi-unit

## Problema

O médico aprova → caso chega a `SCHEDULER_REQUESTED` e **para**: não existe
fila do agendador, os campos de agendamento (`scheduled_unit`/data/hora/local)
não existem no `Case`, e ninguém dispara
`await_scheduling_confirmation`/`confirm_scheduling`/`deny_scheduling`/
`post_final_reply`. As regras de unidade do plano §4 (resposta final
diferenciada, intercorrência só na unidade 1) não têm implementação.

## Objetivo

Entregar o **estágio do agendamento** completo (plano §4/§10 roadmap 08):

1. Campos de agendamento no `Case` (migration única) + serviço transacional
   **confirmar** (unidade 1|2, data/hora/local) e **negar** (motivo
   obrigatório), ambos encadeando `await_scheduling_confirmation →
   (confirm|deny)_scheduling → post_final_reply` no mesmo atomic e postando a
   **resposta final ao NIR** como comunicação na thread do caso — texto padrão
   com data (unidade 1) ou *"Recusar o relatório — caso agendado na Unidade 2,
   que comunicará a Secretaria"* (unidade 2).
2. App `apps/scheduler`: fila completa (sem diferenciação por unidade) com
   detalhe do caso (identificação do paciente + decisões médicas por
   procedimento) e formulários de confirmação/negação.
3. **Intercorrência pós-agendamento (unidade 1 apenas)**: nova transição FSM
   `reopen_scheduling` (`FINAL_REPLY_POSTED → AWAITING_SCHEDULING` — segunda
   transição nova pós-change-03, prevista no guardrail do design D4/03),
   motivo obrigatório, campos de agendamento limpos, evento + comunicação ao
   NIR; **desabilitada para unidade 2**.

## Escopo

**Inclui**: migration `cases/0008` (campos de agendamento); serviços
`confirm_scheduling`/`deny_scheduling`/`reopen_scheduling_after_incident` em
`apps/cases` (ou `apps/scheduler/services.py` — ver design); app
`apps/scheduler` (fila/detalhe/forms/nav); comunicação de resposta ao NIR
(user message na thread existente — visível ao NIR pelo intake do change 04);
spec nova `scheduling`.

**Não inclui**: `nir_acknowledge` em diante (ack/cleanup = change 09);
resposta final de caso **negado pelo médico** (`DOCTOR_DENIED →
post_final_reply` — change 09); re-identificação de artefatos clínicos para o
scheduler (ver decisão D3); dashboard/notificações (11); PWA.

## Sucesso

- Caso aprovado é confirmado com unidade 1|2 + data/hora/local e o NIR vê a
  resposta final correta na thread do caso (texto por unidade), com o caso em
  `FINAL_REPLY_POSTED`.
- Negação exige motivo e produz resposta ao NIR.
- Unidade 1 confirmada pode ser desmarcada por intercorrência (motivo
  obrigatório) e volta à fila para re-agendamento; unidade 2 nunca.
- FSM fechada: nenhum estado novo; uma transição nova (`reopen_scheduling`).

## Capabilities

- `scheduling` (nova) — spec delta em `specs/scheduling/spec.md`.
