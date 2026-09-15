# Tasks — detected-procedures-visible

## 1. Implementação

- [ ] 1.1 Slice 001 — detalhe do NIR: rows de procedimento com origem+detecção; resumo no card de revisão da divergência; testes (divergência, coincidente, sem detecção)
- [ ] 1.2 Slice 002 — detalhe do médico: card «Procedimentos do caso» com todas as rows (inclui detectado não-declarado); teste de ordem atualizado; testes novos

## 2. Validação

- [ ] 2.1 E2E dev: detalhe NIR do caso dd405e6d mostra angio_art_perif (não detectada) × art_perif (detectada); detalhe médico de caso coincidente mostra badges
- [ ] 2.2 Suíte completa + lint + mypy + `openspec validate` strict
