# Slice 004: Reenvio corrigido (novo caso vinculado)

## Objetivo

Reenvio corrigido herdado do ats-web: de um caso **encerrado próprio**, o NIR
cria um **novo caso** (pipeline completo desde `NEW`) vinculado por
`corrects_case` com motivo obrigatório e tipos declarados explícitos; o
original só ganha o evento de supersedição e a listagem "corrigido por".

## Contexto necessário

- Slices 001–003 entregues (caminho até `CLEANED` existe).
- `apps/intake/services.py::create_case_with_documents(*, user, role, files,
  procedure_types)` — cria o caso em `NEW` com documentos/tipos e enfileira o
  worker pdf (inline em teste); validações `_validate_batch`/
  `_validate_declared_types`; `_assert_owned_by` (escopo por criador).
- `apps/cases/events.py` — enum canônico estilo TextChoices (ex.:
  `CASE_PROCEDURES_DECLARED`); aditivo: acrescentar `CASE_CORRECTION_CREATED`
  e `CASE_MARKED_SUPERSEDED` (labels pt-BR).
- `apps/cases/models.py` — como gravar eventos: ver padrão usado pelos
  serviços (`CaseEvent.objects.create` com `actor_type/actor/actor_role/
  event_type/payload` dentro do atomic).
- Migration: cases está em `0008_case_scheduling` → **0009** é a próxima.
- Semântica ats-web (somente-leitura):
  `/projects/dev/ats-web/apps/intake/services.py::create_corrected_resubmission`
  — novo intake, tipos EXPLÍCITOS (nunca herdados), original intacto exceto
  evento.
- Design D4/D5 (`openspec/changes/nir-result-closure/design.md`) — kwargs
  aditivos em `create_case_with_documents`; fronteira com o resubmit do
  change 04.

## Requisitos verificáveis

- **R1** Migration única `apps/cases/migrations/0009_case_correction.py`:
  `corrects_case` (self-FK `SET_NULL`, null, `related_name="corrected_by"`),
  `correction_reason` (`TextField` blank), `correction_created_by` (FK User
  `SET_NULL`, null); sem drift (`makemigrations --check`).
- **R2** `create_corrected_resubmission(*, original_case, user, role, files,
  procedure_types, correction_reason)` em `apps/intake/services.py`:
  valida motivo não vazio (strip), `original_case.status == CLEANED`,
  criador (`_assert_owned_by`), lote e tipos (fontes únicas existentes);
  no MESMO atomic: cria o novo caso via `create_case_with_documents` com os
  kwargs aditivos `corrects_case`/`correction_reason`/`correction_created_by`
  (default `None`/`""` preserva o comportamento do change 04 — regressão
  testada), posta `CASE_MARKED_SUPERSEDED` no original (payload com id do
  novo) e `CASE_CORRECTION_CREATED` no novo (payload com id do original +
  motivo). O original NÃO muda status/dados.
- **R3** Tipos do novo caso são EXATAMENTE os declarados na chamada (não
  herdam os do original — teste com conjuntos distintos).
- **R4** UI: no detalhe de caso `CLEANED` próprio (nir), link/botão
  "Reenviar corrigido" → `intake:case_resubmit` (GET form: arquivos +
  checkboxes de tipos do catálogo + motivo; POST → serviço → redirect ao
  detalhe do novo caso com flash); erros de validação re-renderizam o form;
  o detalhe do original lista `corrected_by` (id/criação/motivo, ordenado).
  Guards: `role_required("nir")`, criador (404 caso alheio), botão somente
  em `CLEANED`.
- **R5** Testes: serviço happy (campos do novo, eventos nos DOIS casos,
  original intacto, **enqueue do worker pdf — testes do serviço com
  `INTAKE_RUN_TASKS_INLINE=False` + assert do enqueue, padrão dos testes
  atuais do intake**; comportamento com inline=True é desvio conhecido
  documentado no design D4); motivo vazio;
  original não-`CLEANED`; não-criador; tipos distintos (R3); regressão do
  `create_case_with_documents` puro (sem kwargs → sem correção, comportamento
  do 04); view flow (GET/POST/erros/lista corrected_by).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/cases/{models,migrations/0009_case_correction}.py` | `test_migration_fields`, `makemigrations --check` |
| R2 | `apps/intake/services.py` | `test_resubmission_creates_linked_case`, `test_resubmission_events_both_cases`, `test_resubmission_original_untouched`, `test_resubmission_validations` |
| R3 | `apps/intake/services.py` | `test_resubmission_types_explicit` |
| R4 | `apps/intake/{views,urls}.py`, `templates/intake/{case_detail,corrected_resubmission}.html` | `test_resubmit_view_flow`, `test_resubmit_button_only_cleaned_own`, `test_resubmit_non_creator_404`, `test_original_lists_corrections` |
| R5 | `apps/intake/tests/test_corrected_resubmission.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/models.py                      # +3 campos D4
  - apps/cases/migrations/0009_case_correction.py
  - apps/cases/events.py                      # +2 eventos canônicos
  - apps/intake/services.py                   # kwargs aditivos + serviço novo
  - apps/intake/views.py                      # view de reenvio + lista corrected_by
  - apps/intake/urls.py                       # +intake:case_resubmit
  - apps/intake/tests/test_corrected_resubmission.py
  - templates/intake/case_detail.html         # botão + lista corrected_by
  - templates/intake/corrected_resubmission.html

out_of_scope:
  - mudar resubmit_case_documents (change 04 — fronteira D5)
  - anexos/OCR (change 10); dashboard (11)
  - events de system-notice (nenhuma projeção nova)
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/intake/tests/test_corrected_resubmission.py`
- Falha esperada: `ImportError: cannot import name 'create_corrected_resubmission'`.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/intake/tests/ apps/cases/tests/` —
  exit 0 (regressão: criação do 04 + FSM/eventos).
- `uv run ruff check apps/intake apps/cases && uv run ruff format --check apps/intake apps/cases`
- `uv run mypy .`
- `uv run python manage.py makemigrations --check --dry-run`

## Critérios de aceitação

- [ ] R1–R5 comprovados; novo caso percorre o pipeline normal desde `NEW`
- [ ] Original permanece `CLEANED` e íntegro (exceto evento + lista)
- [ ] Tipos explícitos nunca herdados; validações antes de qualquer criação
- [ ] Regressão do create_case_with_documents puro coberta
- [ ] Gate parcial do slice verde
