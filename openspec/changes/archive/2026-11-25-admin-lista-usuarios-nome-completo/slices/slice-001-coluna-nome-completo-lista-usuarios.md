# Slice 001 — Coluna «Nome completo» e busca por nome na lista de usuários

## Identity

- Slice: S1
- Change: `admin-lista-usuarios-nome-completo`
- Task: `tasks.md` → S1 — Coluna «Nome completo» e busca por nome na lista
  de usuários do Django admin

## Objective

Fazer a lista de usuários do Django admin mostrar quem é quem (coluna
«Nome completo», com fallback para `username`) e permitir encontrar
usuários pelo nome ou sobrenome.

## Read first

- `AGENTS.md`
- `openspec/changes/admin-lista-usuarios-nome-completo/proposal.md`
- `openspec/changes/admin-lista-usuarios-nome-completo/design.md`
- `openspec/changes/admin-lista-usuarios-nome-completo/specs/account-access/spec.md`
- `apps/accounts/models.py` — `User.display_name`
- `apps/accounts/admin.py` — `UserAdmin` atual
- `apps/accounts/tests/test_ad_upn.py` — `TestAdminRegistration` (padrão
  `admin_client` + `reverse("admin:accounts_user_changelist")`)
- `apps/accounts/tests/test_admin_professional_council.py` — fixtures
  tipadas de `client`/`admin_client` e asserção de estrutura do `UserAdmin`

## Requirements

- R1: `UserAdmin.list_display` expõe a coluna «Nome completo» logo após
  `username`; `username` permanece a primeira coluna e o link de edição.
- R2: A coluna exibe nome e sobrenome do usuário.
- R3: Usuário sem nome/sobrenome: a coluna exibe o `username`.
- R4: A coluna é ordenável: acionar o cabeçalho ordena as linhas pelo
  primeiro nome.
- R5: `UserAdmin.search_fields` alcança `first_name` e `last_name`
  (parcial, sem diferenciar maiúsculas), mantendo `username` e `ad_upn`.
- Evidência: testes automatizados (asserção de estrutura + renderização da
  lista via `admin_client`).

## Expected blast radius

- Esperado: `apps/accounts/admin.py`; teste novo em
  `apps/accounts/tests/` (ex.: `test_admin_user_list_display.py`).
- Sensível/intocável: `apps/accounts/models.py` (nenhuma mudança de modelo),
  migrations (nenhuma), `fieldsets`/`add_fieldsets`, `save_model`,
  `get_urls`, backends/autenticação, middleware, views.
- Comportamento preservado: regra de senha do `save_model`, rotas de senha
  removidas, fieldsets existentes, `filter_horizontal`.
- Expansão de blast radius exige justificativa no relatório.

## Failing-before plan

Failing-before evidence:
- targeted test: `uv run pytest apps/accounts/tests/test_admin_user_list_display.py -x`
  (novo, escrito primeiro)
- expected failure: asserções de estrutura (`"full_name"` ausente de
  `list_display`; `first_name`/`last_name` ausentes de `search_fields`) e de
  renderização (coluna «Nome completo» e `field-full_name` ausentes do HTML)
  falham no código atual.

## Implementation constraints

- Reusar `User.display_name` — nenhuma regra de nome no admin.
- Rótulo em português via `@admin.display(description="Nome completo")`,
  sem `verbose_name` nem migration.
- Não alterar `fieldsets`/`add_fieldsets`/`save_model`/`get_urls`.
- Não declarar `list_display_links` (default do Django: primeira coluna).
- Método tipado (`obj: User) -> str`) para o mypy strict.
- Linha ≤ 100 (ruff format).

## Validation

Focused validation (worker):
- `uv run pytest apps/accounts/tests/test_admin_user_list_display.py -x`
- `uv run pytest apps/accounts -x`

Project-required gates: `AGENTS.md` §2 (ruff, format, mypy, pytest).

Runtime/verification evidence: não exigida (superfície de admin coberta por
teste de renderização; sem jornada verify-hmd para admin).

## Acceptance criteria

- [ ] R1 coluna «Nome completo» após `username`; username é a primeira coluna e o link
- [ ] R2 nome e sobrenome exibidos
- [ ] R3 fallback para `username` sem nome
- [ ] R4 ordenação pelo primeiro nome
- [ ] R5 busca por nome e sobrenome
- [ ] Failing-before evidence registrada
- [ ] Gates do projeto verdes

## Escalation conditions

Pare e retorne `BLOCKED_NEEDS_DECISION` se: contradição com spec/design;
necessidade de migration ou mudança de modelo; pedido de colunas extras,
filtros ou mudança de semântica de login; qualquer expansão material de
escopo.

## Evidence report

`/tmp/hmd-slice-admin-nome-completo-001-report.md` — status
(`READY_FOR_REVIEW`/`BLOCKED_NEEDS_DECISION`), requirements→evidence,
arquivos alterados, failing-before, passing-after, comandos+exit, riscos.

## Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy in `AGENTS.md` and preserve existing
conventions. Prefer the smallest correct change; avoid speculative abstraction
and unrelated refactoring.
Produce failing-before evidence, implement the smallest correct change, run
focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
