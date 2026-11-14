# Detalhe, decisão e confirmação por papel (slice 003)

Prova da jornada completa médico → agendador → NIR no layout ats-web
(ui-parity-intake-detail, slice 003, R1–R3): detalhe do caso (médico,
agendador, NIR), página de trabalho/decisão e confirmação do agendador
com o mesmo conteúdo e comportamento do layout antigo — só classes e
estrutura visual mudam (ordem clínica contratada, decisão atômica,
reservas/heartbeat e validações intactos). Sobre a instância isolada
do `verify-hmd`, com o caso sintético `VERIFY-001`.

## Jornadas

- `doctor-detalhe-decide`: `verify-doctor` logado pelo form → fila
  médica → página de trabalho do `VERIFY-001` com
  `.doctor-decision-layout`, cards `.doctor-report-card`,
  `.doctor-report-grid`, `.report-block-body` e cartão
  `.doctor-decision-form-card`; ordem clínica pinada no DOM
  (Procedimentos → Sumário → Estrutura → Prior-case → Documentos →
  Requisitos → Alertas); reserva com `lock_token` não-vazio;
  aprova todos os procedimentos e *Registrar decisão* → detalhe
  read-only com *Decisões registradas* e *Aprovado*.
- `scheduler-detalhe-confirm`: `verify-scheduler` logado pelo form →
  fila de agendamento → detalhe com `.detail-layout`, cards
  `.report-card`, `.summary-box` e cartões
  `.scheduler-decision-form-card` (confirmar/negar), `lock_token` e
  heartbeat intactos → confirma unidade 1 com data/hora futuras e
  local → detalhe exibe o agendamento.
- `nir-detalhe-before-ack`/`nir-detalhe-cleaned`: `verify-nir` logado
  pelo form → detalhe com `.detail-layout`/`.report-card`/
  `.report-block-body`, seção *Resultado* com as decisões →
  *Confirmar recebimento* → caso concluído (*Caso concluído* /
  *Reenviar corrigido*).

## Como dirigir (Playwright)

```bash
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
node .pi/skills/verify-hmd/scripts/browser/drive-detalhe-papeis.mjs --run <tmp/verification/hmd-verify-...>
```

O drive recusa rodar sem posse da instância (doctor prévio via
`control-hmd doctor`). Capturas: `doctor-detalhe-decide`,
`doctor-detalhe-decide-filled`, `doctor-detalhe-decided`,
`scheduler-detalhe-confirm`, `scheduler-detalhe-confirm-filled`,
`scheduler-detalhe-confirmed`, `nir-detalhe-before-ack`,
`nir-detalhe-cleaned` (+ `report.json`/`observed-state.json`).

## Gotchas

- A página de trabalho do médico reserva o caso (`doctor_decision`) e
  o detalhe do agendador reserva (`scheduler_confirm`) — é o
  comportamento normal de leitura, não prova de decisão. Cleanup
  destrói o banco inteiro, sem vazamento entre runs.
- O caso sintético é setup, nunca prova clínica. `VERIFY-002` fica
  intacto para as demais jornadas da mesma run.
- Ordem condicional populada (`Anexos` + `Recomendação agregada do
  pipeline` entre `Documentos`→`Requisitos`→`Alertas`): provada no pytest
  (`test_doctor_detail_orders_conditional_cards_when_populated`). O drive
  segue conferindo só os cards sempre-presentes: o seed sintético não
  renderiza os condicionais e não há fluxo UI aprovado para popular
  `VERIFY-001` (anexos só no envio de intake; agregado vem do LLM2), e o
  drive veda mutação direta de banco — popular no seed arriscaria as
  jornadas 001/002/004.
