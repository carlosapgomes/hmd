# Tasks

Executar um slice por vez; contratos em `slices/`; TDD RED→GREEN→REFACTOR; quality gate de `AGENTS.md` §2; relatórios em `/tmp/`.

## 1. Slice 001 — Reenvio corrigido por qualquer NIR

Contrato: [slice-001-reenvio-qualquer-nir.md](slices/slice-001-reenvio-qualquer-nir.md). Design D1/D2/D4; specs delta `case-closure` (reenvio) + `intake-nir` (busca/detalhe histórico).

- [x] 1.1 Demonstrar RED: NIR não-criador reenvia caso `CLEANED` (hoje recusado); ação «Reenviar corrigido» no detalhe histórico para `CLEANED` (hoje inexistente); caso fora de `CLEANED` segue recusado; reenviador = criador do novo caso.
- [x] 1.2 Implementar: relaxar guarda de `corrected_resubmission` (papel `nir` + `CLEANED`, sem criador — D1), ação no `closed_case_detail` gated por status (D2), testes (incl. validações de 1 PDF/1 tipo/motivo intactas para não-criador e evento de supersedição com reenviador como autor).
- [x] 1.3 Executar bateria completa e produzir relatório em `/tmp/hmd-reenvio-001-report.md`.

> Nota de execução: loop controlado pela pai (pi-subagents-openspec-loop/2) — implementação 1 rodada + review 1 (BLOCK: 1×P1 — recusa de caso ativo alheio terminava em 404 após redirect; a borda que o worker havia sinalizado foi julgada insuficiente) + fix 1 rodada (refuse_url 3 ramos: próprio→detalhe do criador; alheio histórico→detalhe histórico; alheio fora do escopo→busca; testes seguem redirect e verificam 200+mensagem) + review 2 FINAL («Merge verdict: OK», zero findings); gate pai pós-fix: ruff/format/mypy verdes, pytest 2325 passed; isolamento do fix provado por fingerprints (apenas views.py + test_resubmit_any_nir.py). Evidências: `/tmp/hmd-reenvio-001-report.md`, `/tmp/hmd-reenvio-001-fix-1-report.md`, raiz `reenvio-001-20261121T212534`.

## 2. Slice 002 — Consolidação do nav (opção A)

Contrato: [slice-002-consolidacao-nav.md](slices/slice-002-consolidacao-nav.md). Depende de 001. Design D3/D5; specs delta `case-closure` (resultado/criador) + `intake-nir` (meus casos).

- [x] 2.1 Demonstrar RED: my_cases lista apenas ativos sem navegação Ativos/Encerrados (`?tab=closed` legado cai na default preservando filtros); nav com 3 itens («Buscar Encerrados» renomeado «Casos Encerrados»; badge de encerrados removido); redirect pós-ciência e botão de reenvio no detalhe do criador preservados.
- [x] 2.2 Implementar views/templates (`my_cases` sem aba; `_nav.html` 3 itens; contadores) + manual do usuário atualizado; testes.
- [x] 2.3 Executar bateria completa e produzir relatório em `/tmp/hmd-reenvio-002-report.md`.

> Nota de execução: loop controlado pela pai (pi-subagents-openspec-loop/2) — implementação 1 rodada + review 1 (BLOCK: 1×P1 — resultado+motivo do encerramento administrativo órfou com a aba removida; extensão de escopo a `closed_case_detail` AUTORIZADA pela controladora, spec prevalece sobre lista de sensíveis) + fix 1 rodada (selo «Encerrado administrativamente»+motivo no detalhe histórico via `_administrative_closure_result` reutilizado; CLEANED comum sem selo; teste do cenário da spec) + review 2 FINAL («OK with notes», zero findings); gate pai pós-fix: ruff/format/mypy verdes, pytest 2331 passed; isolamento do fix por fingerprints (views.py + teste; card do template já vinha da implementação). **Residual preexistente (nota do reviewer, não causado por este delta)**: motivo do admin-closure não projetado em `event_detail_lines` após reabertura — candidato a backlog. Evidências: `/tmp/hmd-reenvio-002-report.md`, `/tmp/hmd-reenvio-002-fix-1-report.md`, raiz `reenvio-002-20261121T215229`.

## Encerramento (após 002)

- [x] `openspec validate --strict`, atualizar `PROJECT_CONTEXT.md` no arquivamento.
