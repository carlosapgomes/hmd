# Slice 005 — Doctor + scheduler: referência humana em detalhe/decisão e badge de prior-case

```yaml
expected_files:
  - apps/doctor/presenters.py
  - apps/scheduler/presenters.py
  - templates/doctor/case_detail.html
  - templates/doctor/decide.html
  - templates/doctor/_case_cards.html
  - templates/scheduler/case_detail.html
  - apps/doctor/tests/test_detail.py
  - apps/doctor/tests/test_decision.py
  - apps/scheduler/tests/test_views.py
allowed_incidental_files: []
out_of_scope:
  - conteúdo/ordem dos cards (specs vigentes de leitura clínica)
  - `identification.*` dos presenters (inalterado)
  - queues (já sem uid — change `queues-remove-uid`)
```

## Objetivo

Detalhe e decisão do médico e detalhe do agendador deixam de exibir o uid
completo em `<title>`/breadcrumb/links, usando a referência humana; o badge
do prior-case exibe a referência desambiguada do caso prévio.

## Contexto necessário

- Presenters: `apps/doctor/presenters.py` — `build_case_detail_context`
  monta `"case_id": str(case.case_id)` (~`:713`) e `prior_cases` via
  `_build_prior_cases` (`:451-473`, dict com `prior_case_id`).
  `apps/scheduler/presenters.py` — `"case_id": str(case.case_id)` (`:115`).
  Adicionar em ambos a chave raiz `"case_reference": case.display_reference`
  (helper do slice 001; D4 do design).
- Templates (trocar `case_id` por `case_reference` APENAS em texto visível;
  hrefs continuam com `case_id`):
  `templates/doctor/case_detail.html:4,12`; `templates/doctor/decide.html:4,13`
  (o link do breadcrumb também); `templates/scheduler/case_detail.html:5,13`.
  A view de decisão (`apps/doctor/views.py`) reutiliza o mesmo contexto —
  confirmar onde `case_id` chega ao `decide.html` antes de editar; se a view
  de decisão monta contexto próprio, acrescentar lá também a chave
  `case_reference` (blast radius previsto).
- Prior-case: em `_build_prior_cases`, acrescentar
  `"prior_case_reference"` — lookup read-only do `Case` prévio com
  `.only("case_id", "agency_record_number")` (a query só roda quando há
  prior-case) e usar `prior_case.disambiguated_reference`. Template
  `templates/doctor/_case_cards.html:161` — badge `Caso
  {{ prior.prior_case_id }}` → `Caso {{ prior.prior_case_reference }}`
  (`prior_case_id` permanece no dict — `apps/doctor/tests/test_detail.py:562`
  o pina).
- Tests: `apps/doctor/tests/test_detail.py`, `test_decision.py`,
  `apps/scheduler/tests/test_views.py` — asserts `str(case.case_id) in body`
  continuam válidos (uid no href); revisar pins de `Caso <uid>` em texto.

## Requisitos verificáveis

- **R1** — Doctor detalhe + decisão: `<title>`, breadcrumb e link do
  breadcrumb exibem «Caso <nº>» (fallback `uid <8>`); uid completo ausente
  de texto visível.
- **R2** — Scheduler detalhe: idem para `<title>` e breadcrumb.
- **R3** — Badge do prior-case: exibe
  `Caso <nº do prévio> · <8 chars do uid do prévio>` (ou `uid <8>` quando o
  prévio não tem nº); uid completo do prévio ausente de texto visível.
- **R4** — `prior_case_id` permanece no dict de `prior_cases` (compat com
  pins); `identification.*` inalterado.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/doctor/presenters.py`, `templates/doctor/{case_detail,decide}.html` | testes novos em `test_detail.py`/`test_decision.py` |
| R2 | `apps/scheduler/presenters.py`, `templates/scheduler/case_detail.html` | teste novo em `test_views.py` |
| R3 | `apps/doctor/presenters.py`, `templates/doctor/_case_cards.html` | teste novo em `test_detail.py` (badge `N · <8>`) |
| R4 | `apps/doctor/presenters.py` | `uv run pytest apps/doctor/tests/test_detail.py -q` (pin `:562` segue verde) |

## Plano de testes

### RED

- Comando: `uv run pytest apps/doctor/tests/test_detail.py apps/doctor/tests/test_decision.py apps/scheduler/tests/test_views.py -q`
- Falha esperada: testes novos falham — title/breadcrumb renderizam
  `Caso <uuid>` e o badge usa `prior.prior_case_id` (uid completo).

### GREEN / verificação local

- O mesmo comando do RED — exit 0
- `uv run pytest apps/doctor apps/scheduler -q` — sem regressão nos apps
- `uv run ruff check apps/doctor apps/scheduler && uv run ruff format --check apps/doctor apps/scheduler` — exit 0
- `uv run mypy apps/doctor/presenters.py apps/scheduler/presenters.py` — exit 0

## Critérios de aceitação

- [ ] R1-R4 verdes; uid completo ausente de texto visível nas 3 páginas
- [ ] Hrefs/rotas inalterados (uid como chave)
- [ ] Ordem/conteúdo dos cards intactos (nenhum card clínico tocado)
- [ ] Diff limitado a presenters + 4 templates + 3 módulos de teste

## Handoff

Se a view de decisão montar contexto próprio sem acesso ao `Case`, ou se o
lookup do prévio exigir mais que um `.only()`, parar e escalar ao parent.
