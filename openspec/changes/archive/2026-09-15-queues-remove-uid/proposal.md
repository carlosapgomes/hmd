# Proposal: queues-remove-uid

## Why

Os cards das três filas (meus casos do NIR, fila médica, fila do
agendador) exibem o cabeçalho «Caso <uid-completo>» — desnecessário e
confuso para os usuários (o identificador interno do sistema não tem
significado operacional; o **número de ocorrência**, dado do relatório
SESAB que a equipe conhece, já aparece no card). Decisão do dono
(2026-09-15): remover o uid dos cards das três filas; quando o caso não
tem nº de ocorrência (retido no gate — raro), o card fica **apenas com a
identificação do paciente** (opção (a), sem fallback para uid).

## What Changes

- Remoção do span «Caso <uid>» dos 3 templates de card; o nº de ocorrência
  permanece como identificador único do card (com `—` quando ausente,
  comportamento vigente da linha «Nº de ocorrência»).
- Specs das 3 superfícies pinnam a AUSÊNCIA do uid no card (contrato
  explícito — ninguém reintroduz).

## Impact

- **Specs**: `intake-nir` (meus casos MODIFIED), `doctor-decision` (fila
  médica MODIFIED), `scheduling` (fila do agendador MODIFIED) — cada uma
  com +1 cenário «sem uid».
- **Código**: 3 templates + 3 módulos de teste (atualização dos pins
  existentes + teste de ausência). Sem modelo/migration/views.
- **Fora de escopo**: painel (usa nº de ocorrência com fallback uid curto
  — padrão próprio, decisão anterior) e detalhes de caso (o uid tem papel
  técnico no breadcrumb/descrição).
