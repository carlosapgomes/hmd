# Proposal: queue-cards-wait-time

## Why

Backlog item 1 do dono: os cards das filas devem exibir a identificação do
paciente (nome + idade) e o **tempo de tela**, ordenados pelo tempo de tela
(mais tempo esperando primeiro — padrão ats-web: `wait_minutes` derivado +
`order_by(regulation_days_on_screen desc nulls_last)`). Hoje as filas do
médico (`apps/doctor/views.py` — `order_by("created_at", "case_id")`) e do
agendador (`apps/scheduler/views.py:187` — idem) mostram nome e nº de
ocorrência, mas sem idade nem tempo, em FIFO puro; «Meus casos» do NIR nem
identifica o paciente. Com o change `sesab-header-extraction`, o caso tem
nome, idade e **dias em tela oficiais do cabeçalho SESAB**
(`days_on_screen`) — o tempo do regulador, mais fiel que o tempo desde o
upload.

## What Changes

- **Filas de espera (médico e agendador, todas as abas)**: ordenação por
  `days_on_screen` DESC nulls-last (tempo de tela oficial), desempate
  `created_at`/`case_id` (FIFO) — casos sem cabeçalho vão ao fim, FIFO
  entre si (molde ats-web).
- **Cards das filas (médico/agendador)**: idade (`84 a`) ao lado do nome;
  linha de tempo «⏱ Aguardando há X» (timesince de `created_at`) +
  badge «N d em tela» quando `days_on_screen` presente (fonte oficial).
- **Meus casos (NIR)**: cards ganham nome do paciente e idade (o NIR é o
  criador do caso e manuseia o relatório físico — legitimidade
  operacional); ordenação histórica (`-created_at`) preservada.
- **Painel manager/admin permanece zero-PHI** (decisão vigente: lista por
  nº de ocorrência) — nenhuma mudança no dashboard.

## Impact

- **Specs**: `doctor-decision` (fila médica MODIFIED), `scheduling` (fila
  do agendador MODIFIED), `intake-nir` (meus casos MODIFIED).
- **Código**: `apps/doctor/views.py` + `templates/doctor/queue.html`;
  `apps/scheduler/views.py` + `templates/scheduler/queue.html`;
  `apps/intake/views.py` + `templates/intake/my_cases.html`. Sem
  modelo/migration (campos existem).
- **Backlog**: fecha o item 1; resta o item 4 (trilha no painel) para
  concluir o backlog e liberar release.
