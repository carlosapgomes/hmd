# Slice 001 — Provisionamento do par de conselho no Django admin

## Identity

- Slice: S1
- Change: `registro-profissional-resposta-final`
- Task: `tasks.md` → «S1 — Provisionamento do par de conselho no Django admin»

## Objective

Tornar o par `professional_council`/`professional_council_number` editável
no Django admin (fieldset próprio nos formulários de edição e criação),
destravando o provisionamento operacional do CRM.

## Read first

- `AGENTS.md`
- `openspec/changes/registro-profissional-resposta-final/proposal.md`
- `openspec/changes/registro-profissional-resposta-final/design.md` — D6
- `openspec/changes/registro-profissional-resposta-final/specs/account-access/spec.md`
- `apps/accounts/models.py` — `User` (`professional_council`,
  `professional_council_number`, `clean()`, `professional_registration_display`)
- `apps/accounts/admin.py` — `UserAdmin` atual (sem o par)
- `apps/accounts/tests/test_ad_upn.py` — padrão de teste de admin com
  `admin_client` (linhas ~111–143) a espelhar
- Referência de padrão (somente-leitura): `/projects/dev/ats-web/apps/accounts/admin.py`
  (fieldset «Profissional»)

## Requirements

- R1: O formulário de EDIÇÃO do usuário no Django admin expõe
  `professional_council` e `professional_council_number` juntos numa seção
  própria («Profissional»), em `fieldsets`.
- R2: O formulário de CRIAÇÃO (`add_fieldsets`) expõe o mesmo par na mesma
  seção.
- R3: Salvar o par completo persiste os dois valores e
  `professional_registration_display` devolve `"{conselho} {número}"`.
- R4: Salvar parcial (conselho sem número) é rejeitado pelo formulário do
  admin com erro apontando o campo pendente (validação par-ou-nada do modelo
  via `clean()`); nada é persistido.
- R5: Salvar os dois vazios persiste vazio e o registro formatado devolve
  `""`.
- Evidência: testes automatizados do admin (admin client ou teste de
  formulário do Django admin) para R3–R5; R1–R2 por asserção da estrutura de
  fieldsets/add_fieldsets (padrão de teste já usado no app).

## Expected blast radius

- Esperado: `apps/accounts/admin.py`; teste novo em
  `apps/accounts/tests/` (ex.: `test_admin_professional_council.py`).
- Sensível/intocável: `apps/accounts/models.py` (nenhuma mudança de modelo),
  migrations (nenhuma), `backends.py`/autenticação, middleware, views.
- Comportamento preservado: regra `save_model` (senha inutilizável com
  `ad_upn`), fieldsets existentes («Acesso institucional»), `filter_horizontal`.
- Expansão de blast radius exige justificativa no relatório.

## Failing-before plan

Failing-before evidence:
- targeted test: `uv run pytest apps/accounts/tests/test_admin_professional_council.py -x`
  (novo, escrito primeiro)
- expected failure: asserção de que o par aparece em `fieldsets`/`add_fieldsets`
  falha (campos ausentes hoje), e/ou POST do admin não persiste o par.

## Implementation constraints

- Espelhar o molde ats-web SEM copiar às cegas: rótulo da seção em português,
  mesmos nomes de campos, sem campos extras.
- Não adicionar validação duplicada no admin — a par-ou-nada vive no modelo
  (`clean()`); o admin só a superficia.
- Não alterar `list_display`/`search_fields` além do estritamente necessário
  (não é requisito).

## Validation

Focused validation (worker):
- `uv run pytest apps/accounts/tests/test_admin_professional_council.py -x`
- `uv run pytest apps/accounts -x`

Project-required gates: `AGENTS.md` §2 (ruff, format, mypy, pytest).

Runtime/verification evidence: não exigida (superfície de admin coberta por
teste de formulário/POST; sem jornada verify-hmd para admin).

## Acceptance criteria

- [ ] R1 fieldsets expõem o par em seção própria
- [ ] R2 add_fieldsets idem
- [ ] R3 par completo persiste e formata
- [ ] R4 parcial rejeitado com erro no campo pendente, sem persistir
- [ ] R5 vazio+vazio persiste vazio, display `""`
- [ ] Failing-before evidence registrada
- [ ] Gates do projeto verdes no escopo tocado

## Escalation conditions

Pare e retorne `BLOCKED_NEEDS_DECISION` se: contradição com spec/design;
necessidade de migration ou mudança de modelo; requisito de UI self-service
de registro; qualquer expansão material de escopo.

## Evidence report

`/tmp/hmd-slice-registro-001-report.md` — status
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
