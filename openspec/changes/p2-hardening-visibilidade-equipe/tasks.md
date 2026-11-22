# Tasks

Micro-change de quitança (skip_specs). Dois slices sequenciais; ordem numérica = execução.

- [x] S1 — Convergência do re-check de papel (helper com mensagem por ação) + remoção do parâmetro `user` morto (paridade provada por testes intocados)
  Contract: slices/slice-001-convergencia-recheck-limpeza.md
- [x] S2 — Cobertura (negação alheia no partial; teste dedicado AWAITING_NIR_ACK) + precisão do manual do agendador
  Contract: slices/slice-002-cobertura-gate-manual.md

## Gate final (controlador)

- [x] G — Após S2: gate completo de `AGENTS.md` §2 e `openspec validate p2-hardening-visibilidade-equipe --strict` (pós-archive: `openspec validate --specs --strict`)
