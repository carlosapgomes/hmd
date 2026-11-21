# Tasks

Executar um slice por vez; não implantar commits intermediários. Contratos em `slices/` são parte do planejamento, não autorização para implementar. Todas as tarefas estão pendentes. Cada slice exige RED → GREEN → REFACTOR e o quality gate de `AGENTS.md`; relatórios ficam em `/tmp/`, não no Git.

## 1. Slice 001 — Sumário completo ao agendador

Contrato: [slice-001-sumario-completo-agendador.md](slices/slice-001-sumario-completo-agendador.md). Design D1/G1; spec delta `scheduling` («Visão do agendador limitada ao necessário» MODIFIED).

- [x] 1.1 Demonstrar RED: detalhe do agendador exibe o sumário completo re-identificado (cenário «Agendador vê o sumário completo re-identificado») — hoje só a primeira linha aparece; registrar falha antes de qualquer implementação.
- [x] 1.2 Remover o truncamento à primeira linha no presenter do agendador (re-identificação exclusivamente na renderização, D3 do design anterior preservado) e ajustar o template; verificar cenários «sumário completo» e «sem artefatos além do permitido».
- [x] 1.3 Executar bateria completa (`ruff check`, `ruff format --check`, `mypy`, `pytest`) e produzir relatório em `/tmp/hmd-slice-001-report.md`.

> Nota de execução: loop worker(muse-spark-1.3-contributor:high)+reviewer(gpt-6.1-terra:high), 4 rodadas — rodadas 1–3 corrigiram a fidelidade de quebras (linhas vazias, bordas, `linebreaksbr` 1:1); o único BLOCKER recorrente era o ambiente de review sem execução de comandos, suprido pelo gate independente do supervisor (ruff/format/mypy verdes; pytest 2221 passed) — relatório em `/tmp/hmd-slice-001-report.md`.

## 2. Slice 002 — Fundação da intercorrência pós-encerramento

Contrato: [slice-002-fundacao-intercorrencia.md](slices/slice-002-fundacao-intercorrencia.md). Design D2/D3/D4/D8/D9; specs delta `post-closure-issue` (R1–R3) + `notifications` (MODIFIED — fan-out na reabertura).

- [x] 2.1 Demonstrar RED: elegibilidade (CLEANED + unidade 1 + dados retidos por marcador persistente + sem issue ativa; minimizado/purgado inelegível), abertura atômica `CLEANED → AWAITING_SCHEDULING` com payload de motivo livre + snapshot, e fan-out de notificação aos agendadores na reabertura (hoje só o criador é notificado) — serviço, transição e colunas não existem; registrar falhas.
- [x] 2.2 Implementar migration aditiva (campos de issue ativa + `clinical_data_disposed_at` com backfill + índices de busca), gravação do marcador na minimização e na purga (mesmo atomic), transição FSM `reopen_post_closure`, serviço `open_post_closure_issue` (atomic + `select_for_update` + revalidação, limpeza dos 5 campos com snapshot administrativo no evento, `scheduling_reopen_reason` reutilizado, motivo interpolado na thread — padrão do desmarcar, decisão D3) e o evento `CASE_POST_CLOSURE_ISSUE_OPENED` no catálogo da trilha.
- [x] 2.3 Implementar a extensão do marco de reabertura em `apps/accounts/notifications.py` (criador E fan-out agendadores ativos, preview fixo novo, idempotência) e a zeragem da issue em qualquer chegada a `CLEANED` (ciência e encerramento administrativo, mesmo atomic, evento próprio); verificar por testes destinatários/idempotência e a corrida abertura×encerramento sob lock.
- [x] 2.4 Executar bateria completa e produzir relatório em `/tmp/hmd-slice-002-report.md`.

> Nota de execução: loop controlado pela pai (protocolo pi-subagents-openspec-loop/2) — worker(muse-spark-1.3-contributor:high) 1 rodada + reviewer(gpt-6.1-terra:high) 1 rodada, veredito «OK with notes»; gate independente executado pela pai (ruff/format/mypy verdes; pytest 2247 passed). Blast radius expandido justificadamente: `apps/cases/events.py` (2 tipos de evento) e `apps/cases/procedure_review.py` (`event_detail_lines`, fonte única das linhas da trilha). **P2 deferido** (review): falta teste da corrida entre DUAS aberturas concorrentes (`open_post_closure_issue`×`open_post_closure_issue` — o teste existente cobre abertura×encerramento administrativo); endereçar no slice 003 ou polimento futuro. Evidências: `/tmp/hmd-slice-002-report.md` e raiz `slice-002-20261121T163722`.

## 3. Slice 003 — Busca NIR de encerrados e abertura de intercorrência

Contrato: [slice-003-busca-nir-encerrados.md](slices/slice-003-busca-nir-encerrados.md). Depende de 002. Design D1/D6; specs delta `intake-nir` (ADDED) + `post-closure-issue` (superfície).

- [ ] 3.1 Demonstrar RED: busca por nome/nº sobre encerrados + intercorrências ativas de qualquer criador (cap 50, estado vazio, referência humana, 403 para não-NIR), detalhe histórico read-only com trilha e POST de abertura para elegíveis.
- [ ] 3.2 Implementar views/urls/form/templates próprios com guarda de escopo histórico (guard criador do detalhe vigente intocado), elegibilidade exibida na busca e no detalhe, flash + redirect no POST; verificar todos os cenários do delta `intake-nir`.
- [ ] 3.3 Executar bateria completa e produzir relatório em `/tmp/hmd-slice-003-report.md`.

## 4. Slice 004 — Histórico do agendador e carimbos de decisão de agendamento

Contrato: [slice-004-historico-agendador.md](slices/slice-004-historico-agendador.md). Depende de 002. Design D5 (par de agendamento)/D6; spec delta `scheduling` (ADDED «Histórico do agendador»).

- [ ] 4.1 Demonstrar RED: carimbos `scheduling_decided_at/by` (confirmação E negação carimbam no atomic; hoje a negação não carimba nada), backfill legado por eventos, e a busca por nome/nº sobre o universo de processados (incluindo CLEANED e issue ativa, qualquer decisor, cap 50, negativa médica encerrada excluída, estado vazio, referência humana), detalhe em leitura sem ações com gate de PDF, 403 para não-agendador.
- [ ] 4.2 Implementar migration (carimbos + backfill por eventos com fail-closed de ator nulo), carimbo nos serviços de confirmação/negação (reabertura NÃO zera), view/urls/template do histórico com queryset por `scheduling_decided_at` e link de detalhe em modo leitura; verificar cenários do delta.
- [ ] 4.3 Executar bateria completa e produzir relatório em `/tmp/hmd-slice-004-report.md`.

## 5. Slice 005 — Abas «Hoje» (entregar por último)

Contrato: [slice-005-abas-hoje.md](slices/slice-005-abas-hoje.md). Depende de 003 e 004 (superfícies de arquivo já existem). Design D5/D7; specs delta `scheduling` («Fila do agendador completa» MODIFIED) + `doctor-decision` (MODIFIED).

- [ ] 5.1 Demonstrar RED: «Decididos Hoje» (decisões do dia corrente pelo próprio médico, ordenação por data/hora desc, caso de ontem fora, caso encerrado no mesmo dia dentro) e «Processados Hoje» (análogos por `scheduled_by`), com contadores e títulos atualizados.
- [ ] 5.2 Implementar carimbos `doctor_decided_at`/`doctor_decided_by` no `Case` (mesmo atomic da decisão; migration com backfill auditável por eventos — último `CASE_STATUS_DOCTOR_*`, ator nulo fail-closed — + teste anti-drift) e re-escopar as consultas das abas nos dois apps por janela do dia local (médico: `doctor_decided_by/at`; agendador: `scheduling_decided_by/at` do slice 004) em fila, partial, contadores, templates e manual do usuário (incl. registro da perda aprovada da visão de casos antigos pelo médico).
- [ ] 5.3 Executar bateria completa e produzir relatório em `/tmp/hmd-slice-005-report.md`.

## Encerramento (após 005)

- [ ] Revisar deltas contra o comportamento entregue, `openspec validate --strict`, atualizar `PROJECT_CONTEXT.md` no arquivamento e registrar a emenda ao ADR-0010 (D2) na trilha de decisões.
