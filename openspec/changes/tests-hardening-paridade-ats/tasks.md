# Tasks

Executar um slice por vez; contratos em `slices/` são parte do planejamento. TDD conforme design D1 (failing-before N/A — pin de regressão); quality gate de `AGENTS.md` §2; relatórios em `/tmp/`, não no Git.

## 1. Slice 001 — Endurecimento de testes de paridade (P2s dos slices 004/005)

Contrato: [slice-001-endurecimento-testes.md](slices/slice-001-endurecimento-testes.md). Design D1/D2/D3; mudança somente de testes (skip_specs).

- [x] 1.1 Implementar os três testes de regressão (histórico pelo caminho real com `open_post_closure_issue`; `CLEANED`+PDF+mesmo decisor → cards ausentes + 404; fluxo de backfill 0014 → carimbos com ator → listagem na aba «Decididos Hoje» do decisor correto) e verificar focused verde.
- [x] 1.2 Executar bateria completa (`ruff check`, `ruff format --check`, `mypy`, `pytest`) confirmando que NENHUM arquivo de produção mudou; produzir relatório em `/tmp/hmd-tests-hardening-001-report.md`.

> Nota de execução: loop controlado pela pai (pi-subagents-openspec-loop/2) — implementação 1 rodada + review 1 («Merge verdict: OK», zero findings, sem ciclo de fix); gate pai: ruff/format/mypy verdes, pytest 2314 passed; diff restrito aos 2 arquivos de teste (zero produção). P2 #2 (slice 004) e P2 #3 (slice 005) da change de paridade QUITOS. Residual aceito (design D2): backfill exercitado por função, não pelo plano de migração.

## Encerramento (após 001)

- [ ] `openspec validate --strict`, atualizar `PROJECT_CONTEXT.md` no arquivamento (quitar P2 #2/#3 da change de paridade; P2 #1 permanece documentação).
