# Tasks

Executar um slice por vez; não implantar commits intermediários. Contratos em `slices/` são parte do planejamento, não autorização para implementar. Todas as tarefas estão pendentes. Cada slice exige RED → GREEN → REFACTOR e o quality gate de `AGENTS.md`; relatórios ficam em `/tmp/`, não no Git.

## 1. Slice 001 — Fundação compartilhada da trilha

Contrato: [slice-001-fundacao-compartilhada.md](slices/slice-001-fundacao-compartilhada.md). Design D1/D2/D3(painel)/D6.

- [x] 1.1 Demonstrar RED do builder (`build_event_trail_context`) e do teste anti-drift migrado em `apps/cases/tests/test_event_trail.py` — módulo `apps.cases.event_trail` não existe; registrar falha antes de qualquer implementação.
- [x] 1.2 Mover `EVENT_LABELS`/`EVENT_BADGE_CSS`/helpers para `apps/cases/event_trail.py` (remover `apps/dashboard/event_labels.py`), implementar o builder puro com `select_related("actor")` e atualizar imports do dashboard; verificar R1.
- [x] 1.3 Extrair o partial `templates/cases/_event_trail.html` 1:1 do card do painel e trocar o bloco inline por include, chave `events`→`event_trail`; verificar R2/R3 com a suíte do dashboard VERDE sem editar testes de renderização do detalhe.
- [x] 1.4 Executar bateria completa (`ruff check`, `ruff format --check`, `mypy`, `pytest`) e o grep de gates; produzir relatório em `/tmp/`.

## 2. Slice 002 — Trilha no detalhe do NIR e do médico

Contrato: [slice-002-nir-medico.md](slices/slice-002-nir-medico.md). Depende de 001. Design D3/D4/D5/D6; specs delta `intake-nir` + `doctor-decision`.

- [x] 2.1 Demonstrar RED nos cenários das specs: NIR vê trilha fechada por padrão com ator/papel pt-BR de decisão médica e agendamento; médico (decisão e decidido) vê trilha ao final; FAILED mantém badge sem motivo técnico.
- [x] 2.2 Implementar NIR: `event_trail` em `_assemble_case_detail_context` + include ao final do template; badge de erro intocado; verificar R1.
- [x] 2.3 Implementar médico: `event_trail` no presenter + derivação de `decision_event` da lista (query dedicada removida, card inalterado) + include ao final; verificar R2.
- [x] 2.4 Reescrever os asserts de ausência (`test_my_cases`, `test_detail`, `test_decision`, `test_role_labels`) para presença com propriedades contratuais (fechado por padrão, rótulo, ator, papel, data); verificar R3.
- [x] 2.5 Executar bateria completa e gates do slice; produzir relatório em `/tmp/`.

> Nota de execução (decisão do dono 2026-11-21, opção B): durante o 2.5 o worker bloqueou no pin de PHI do
> `test_procedure_review_recovery.py` (justificativa livre no detalhe NIR × paridade D4); decisão B registrada no
> design D4, expected_files ampliado para 9 (re-escopo do pin) e concluído por fix-worker fresh. P2s deferidos:
> (1) assert positivo fixa só o rótulo «Justificativa:», não o valor livre; (2) gate (a) literal do slice inexequível
> (título vive no partial — check real é `_event_trail`).

## 3. Slice 003 — Trilha no detalhe do agendador

Contrato: [slice-003-agendador.md](slices/slice-003-agendador.md). Depende de 001. Design D3/D4; specs delta `scheduling` + `account-access` (superfícies de rótulo). Embute os P2s deferidos do slice 002.

- [x] 3.1 Demonstrar RED: detalhe do agendador renderiza «Trilha de eventos» fechado por padrão com criação (NIR) e decisão médica (médico) nomeados com papel pt-BR (cenário do delta `account-access`); one-liner e restrições de pipeline preservados.
- [x] 3.2 Implementar: `event_trail` em `build_scheduler_case_detail_context` + include ao final, fora dos gates de PDF/texto; verificar R1.
- [x] 3.3 Apertar o pin da decisão B (P2 deferido): assert positivo do `test_procedure_review_recovery.py` passa a fixar fragmento do valor da justificativa em `follow.content` (sem RED — comportamento já verde); executar bateria completa e gates do slice; produzir relatório em `/tmp/`.

> Nota de execução do 003: P2 deferido pela revisão — o teste de autoria valida atores/papéis/datas de forma
> agregada (uma única data checada); endurecer isolando cada item da trilha por rótulo e verificando nele o
> trio ator+papel+timestamp (candidata a polimento futuro, fora dos loops concluídos).

## Encerramento (após 003)

- [ ] Revisar deltas contra o comportamento entregue, `openspec validate` strict, atualizar `PROJECT_CONTEXT.md` no arquivamento e registrar no design a reversão formal da decisão D4 do `painel-ats-parity`.
