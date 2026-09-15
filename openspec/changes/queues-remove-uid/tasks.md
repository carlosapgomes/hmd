# Tasks: queues-remove-uid

## 1. Slice 001 — Remoção do uid dos cards das filas

- [x] 1.1 Span «Caso <uid>» removido dos 3 templates (my_cases,
      doctor/queue, scheduler/queue) — nenhuma outra mudança
- [x] 1.2 Testes RED→GREEN (ausência do uid no CORPO do card nas 3 filas;
      caso sem ocorrência → `—` + nome, sem uid) + pins atualizados +
      bateria completa

## 2. Encerramento

- [x] 2.1 E2E no dev: 3 filas renderizando sem uid
- [x] 2.2 Archive do change (sync specs main + Purposes se aplicável)
