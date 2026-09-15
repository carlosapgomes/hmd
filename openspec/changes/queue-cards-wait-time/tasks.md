# Tasks: queue-cards-wait-time

## 1. Slice 001 — Filas de espera ordenadas por tempo de tela (médico + agendador)

- [ ] 1.1 Ordenação `days_on_screen` desc nulls-last + desempate FIFO nas
      filas de médico e agendador (todas as abas)
- [ ] 1.2 Cards com nome + idade (`84 a`), ⏱ tempo de espera (timesince) e
      badge `N d em tela` quando presente
- [ ] 1.3 Testes RED→GREEN (ordenação discriminante 10/3/None nas duas
      filas; cards; FIFO existentes atualizados) + bateria completa do
      AGENTS.md

## 2. Slice 002 — Meus casos do NIR com identificação do paciente

- [ ] 2.1 Items com `patient_name`/`patient_age`; card com nome (`—` quando
      ausente) + idade; ordenação histórica preservada
- [ ] 2.2 Testes RED→GREEN (identificação, ausência, escopo por criador) +
      bateria completa

## 3. Encerramento

- [ ] 3.1 E2E no dev: filas do caso real (nome/84 a/6 d em tela/sort) e
      meus casos do NIR identificado
- [ ] 3.2 Archive do change (sync specs main + Purposes)
