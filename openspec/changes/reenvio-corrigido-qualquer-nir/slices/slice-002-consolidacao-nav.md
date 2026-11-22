# Slice 002 — Consolidação do nav (opção A)

## 1. Identity

- Slice: `slice-002-consolidacao-nav`
- Change: `reenvio-corrigido-qualquer-nir`
- Task: `tasks.md` § 2 — depende do slice 001.

## 2. Objective

Nav do NIR com 3 itens: «Meus Casos» lista apenas ativos (sem navegação
Ativos/Encerrados), «Buscar Encerrados» renomeado para «Casos Encerrados» (busca
global), badge de encerrados removido — paridade com o nav do ats-web.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/reenvio-corrigido-qualquer-nir/design.md` (D3/D5)
- Deltas: `specs/intake-nir/spec.md` (meus casos MODIFIED) e `specs/case-closure/spec.md` (resultado/criador MODIFIED)
- `apps/intake/views.py` — `my_cases`/`my_cases_partial` (`_MY_CASES_TABS`, tab param ~124-126, queryset ~390-392, contadores), redirect pós-ciência
- `templates/intake/_nav.html` (4 itens atuais, badges), `templates/intake/my_cases.html` e `_my_cases_content.html` (header de abas), `templates/accounts/manual.html` (seção NIR)
- Paridade: `/projects/dev/ats-web/templates/intake/_nav.html` (3 itens; «Casos Encerrados» → `closed_cases_search`)

## 4. Requirements

- R1: `my_cases` lista **apenas ativos** (tudo exceto `CLEANED`); parâmetro `?tab`
  (incluindo `closed` legado) cai na default de ativos preservando filtros; header
  sem navegação Ativos/Encerrados; contagem como cabeçalho neutro dos ativos.
- R2: `_nav.html` do NIR com 3 itens — «Novo Encaminhamento», «Meus Casos» (badge
  `active_count`), «Casos Encerrados» → rota `closed_cases_search` inalterada
  (renomeio apenas do rótulo); badge `closed_count` removido.
- R3: acessos do criador preservados: redirect pós-ciência ao detalhe do caso
  (URL direta), botão de reenvio no detalhe encerrado do criador (slice 001 manteve),
  notificações — tudo sem dependência da aba removida.
- R4: manual do usuário atualizado (nav, sem aba de encerrados por criador, reenvio
  por qualquer NIR — menção conjunta ao slice 001).
- Evidência: testes cobrindo R1–R3 (lista só ativos; `?tab=closed` fallback;
  nav 3 itens com rótulo novo; redirect pós-ack intacto).

## 5. Expected blast radius

- Esperado: `apps/intake/views.py` (my_cases: tab/queryset/contadores),
  `templates/intake/{_nav,my_cases,_my_cases_content}.html`,
  `templates/accounts/manual.html`, testes (incl. `apps/accounts/tests/test_*` que
  fixam nav/contadores — re-scope legítimo).
- Sensível (não mudar sem escalada): `closed_cases_search`/`closed_case_detail`
  (slice 001), guards por criador, rotas/URLs (nenhuma muda), fluxo de ciência.
- Comportamento preservado: filtros/partial/estado vazio/cards; busca global.

## 6. Failing-before plan

- Targeted tests: (a) my_cases com 2 ativos + 1 `CLEANED` lista só os 2, sem pills
  Ativos/Encerrados, e `?tab=closed` cai nos ativos com filtros (hoje: lista o
  encerrado — falha); (b) nav renderiza 3 itens com «Casos Encerrados» apontando
  para a busca, sem badge de encerrados (hoje: 4 itens — falha); (c) redirect
  pós-ciência preservado (já verde — pin de regressão).
- Expected failure: registrar antes de implementar.

## 7. Implementation constraints

- Nenhuma rota/URL muda (renomeio de rótulo apenas); sem migration.
- Não remover testes de comportamento preservado — re-scopar os que codificam a aba.
- Contadores de notificações/dashboard inalterados (só os do nav do intake).

## 8. Validation

- Focused: `uv run pytest apps/intake apps/accounts -k "my_cases or nav or manual"`.
- Project gates: bateria completa.
- Runtime: não exigida.

## 9. Acceptance criteria

- [ ] R1: só ativos; fallback de tab; sem navegação de abas; filtros preservados.
- [ ] R2: nav 3 itens; rótulo «Casos Encerrados»; URL da busca inalterada; badge closed removido.
- [ ] R3: redirect pós-ciência e porta de reenvio do criador verdes.
- [ ] R4: manual atualizado.
- [ ] Quality gate completo verde.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: algum teste/superfície fora do previsto
depender da aba closed (ex.: dashboard, notificações); se o renomeio colidir com
spec `ui-shell`/`pwa-manual` de forma não prevista.

## 11. Evidence report

`/tmp/hmd-reenvio-002-report.md` (estrutura canônica).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
