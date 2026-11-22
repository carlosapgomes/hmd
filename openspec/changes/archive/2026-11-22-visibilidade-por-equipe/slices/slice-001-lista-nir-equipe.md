# Slice 001 — Lista NIR «Casos em Processamento» em escopo de equipe

## 1. Identity

- Slice: `slice-001-lista-nir-equipe`
- Change: `visibilidade-por-equipe`
- Task: `tasks.md` S1

## 2. Objective

A lista do NIR passa a exibir todos os casos ativos (não-`CLEANED`) de **qualquer criador** (equipe), com o rótulo novo «Casos em Processamento» em todas as superfícies.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/visibilidade-por-equipe/proposal.md` — What Changes (NIR) e Impact
- `openspec/changes/visibilidade-por-equipe/design.md` — D1 (guard de papel), D2 (rótulos + capitalização), D6 (inversão de testes), D8 (títulos antitéticos)
- `openspec/changes/visibilidade-por-equipe/specs/intake-nir/spec.md` — MODIFIED «Meus casos e detalhe do NIR» (cenários da LISTA), «Busca de casos encerrados e detalhe histórico» (cenário do nav), «Badge de transporte nos cards de meus casos»
- `apps/intake/views.py` — `_build_my_cases_context`, `my_cases`, `my_cases_partial`
- `templates/intake/` — `_nav.html`, `my_cases.html`, `_my_cases_content.html`, `home.html` (alerta pós-lote), `case_detail.html` (breadcrumb)
- `templates/accounts/manual.html` — seção NIR
- Testes: `apps/intake/tests/test_my_cases.py`, `test_my_cases_filters.py`, `test_page_titles.py`, `test_nav_consolidation.py`, `test_closed_cases_search.py`, `apps/accounts/tests/test_manual.py`

## 4. Requirements

- R1: a lista lista casos ativos de qualquer criador para qualquer usuário com papel
  ativo `nir`; contagem do header conta todos os ativos da equipe; `?tab=closed`
  legado preserva o fallback seguro de ativos com filtros.
- R2: rótulo «Casos em Processamento» em nav (`_nav.html`), `<h2>` do
  `_my_cases_content.html`, alerta pós-lote de `home.html` e breadcrumb de
  `case_detail.html` — title case; `<title>`/`<h1>` da página em sentence case
  («Casos em processamento», D2); estado vazio informa ausência de casos em
  processamento.
- R3: sufixo de desambiguação de nº duplicado, filtros AND e auto-refresh 20 s
  preservados (agora sobre o universo da equipe).
- R4: papel ativo não-`nir` continua barrado (403/redirect do guard vigente);
  guard de intranet intocado.
- R5: manual (seção NIR) atualizado — lista em escopo de equipe, rótulo novo;
  descrição de notificações individuais (criador) permanece precisa.

## 5. Expected blast radius

- `apps/intake/views.py` (só `_build_my_cases_context` e docstrings adjacentes),
  `templates/intake/{_nav,my_cases,_my_cases_content,home,case_detail}.html`,
  `templates/accounts/manual.html`, testes listados acima.
- **Não muda**: detalhe/ações do caso (slices 002–004), `case_ack`/gate/serve,
  specs de `case-closure`/`doctor`/`scheduler`, `queue_counters`, FSM.
- Comportamento anterior que permanece: ordenação `-created_at`, paginação do
  partial, universo não-`CLEANED`, referência humana sem uid completo.

## 6. Failing-before plan

Failing-before evidence:
- targeted test (novo ou invertido em `test_my_cases.py`): NIR B abre a lista com
  caso ativo criado por NIR A → espera ver o card. Falha hoje com 404 de escopo
  (queryset `created_by=user` retorna vazio → card ausente).
- targeted test de rótulo (`test_page_titles.py`/`test_nav_consolidation.py`):
  espera «Casos em processamento»/«Casos em Processamento» — falha hoje com o
  texto antigo.

## 7. Implementation constraints

- Sem mudança de schema/FSM; sem tocar serviços (defesa em profundidade vem no
  slice 003/004 — não antecipar).
- Não implementar escopo de detalhe/PDF (slice 002).
- Siga a política de engenharia de `AGENTS.md`; menor mudança correta.

## 8. Validation

**Focused validation** — `uv run pytest apps/intake/tests/test_my_cases.py
apps/intake/tests/test_my_cases_filters.py apps/intake/tests/test_page_titles.py
apps/intake/tests/test_nav_consolidation.py apps/intake/tests/test_closed_cases_search.py
apps/accounts/tests/test_manual.py`

**Project-required gates** — os de `AGENTS.md` §2 ao reportar conclusão.

**Runtime/verification evidence** — não exigida (comportamento coberto por testes
de integração Django).

## 9. Acceptance criteria

- [ ] R1: lista/contagem equipe; fallback `?tab=closed` preservado (teste)
- [ ] R2: rótulo nas 4 superfícies + título sentence case + estado vazio (testes de string exata)
- [ ] R3: sufixo duplicado/filtros/auto-refresh preservados sobre universo equipe (testes)
- [ ] R4: não-`nir` barrado como hoje (teste preservado/invertido só no eixo criador)
- [ ] R5: manual NIR atualizado (teste do manual)
- [ ] Failing-before evidence no relatório; quality gate §2 executado

## 10. Escalation conditions

Pare com `BLOCKED_NEEDS_DECISION` se: spec/design contradisserem o código real;
surfir decisão de produto nova (ex.: comportamento de contadores divergente);
necessidade de tocar superfícies fora do blast radius sem justificativa clara.

## 11. Evidence report

`/tmp/hmd-slice-vis-001-report.md` — status (`READY_FOR_REVIEW` |
`BLOCKED_NEEDS_DECISION`), R→evidência, arquivos alterados, failing-before,
passing-after, comandos+exit, expansão de blast radius justificada, riscos.

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-vis-001-report.md`.
