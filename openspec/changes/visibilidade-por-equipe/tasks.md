# Tasks

Cada slice é o contrato executável (worker fresh + reviewer independente). Ordem
numérica = ordem de execução; cada slice deixa o repositório coerente para o
próximo.

- [x] S1 — Lista NIR «Casos em Processamento» em escopo de equipe (rótulos, contagem, manual NIR)
  Contract: slices/slice-001-lista-nir-equipe.md
- [ ] S2 — Detalhe e PDFs/anexos do NIR em escopo de equipe (guard de papel)
  Contract: slices/slice-002-detalhe-pdfs-nir-equipe.md
- [ ] S3 — Gate + confirmação de divergência por qualquer `nir` (serviços: re-check de papel, PermissionError→403)
  Contract: slices/slice-003-gate-confirmacao-equipe.md
- [ ] S4 — Ciência por qualquer `nir` (closure + case_ack) e reenvios corrigidos sem partição próprio/alheio
  Contract: slices/slice-004-ciencia-reenvios-equipe.md
- [ ] S5 — «Decididos Hoje» da especialidade (isnull=False, subtipo, decisor no card, badge, manual médico)
  Contract: slices/slice-005-decididos-hoje-especialidade.md
- [ ] S6 — «Processados Hoje» da equipe (isnull=False, decisor no card, badge)
  Contract: slices/slice-006-processados-hoje-equipe.md
- [ ] S7 — PDF/texto do agendador por decisão de agendamento (equipe) + manual agendador
  Contract: slices/slice-007-pdf-agendador-decisao.md

## Gate final (controlador)

- [ ] G — Após S7: gate completo de `AGENTS.md` §2 (`ruff check` && `ruff format --check` && `mypy` && `pytest`) e `openspec validate visibilidade-por-equipe --strict` (pós-archive: `openspec validate --specs --strict`)
