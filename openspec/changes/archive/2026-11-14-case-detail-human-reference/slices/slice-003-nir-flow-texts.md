# Slice 003 — Textos do fluxo NIR: flash messages, alerta de lote e «Reenviar corrigido»

```yaml
expected_files:
  - apps/intake/views.py
  - templates/intake/corrected_resubmission.html
  - templates/intake/home.html
  - apps/intake/tests/test_creation.py
  - apps/intake/tests/test_corrected_resubmission.py
  - apps/intake/tests/test_gate_actions.py
  - apps/intake/tests/test_closure_views.py
allowed_incidental_files: []
out_of_scope:
  - detalhe do caso (slice 002) e my_cases (slice 004)
  - qualquer texto/logger/URL — só o texto VISÍVEL das mensagens/templates
```

## Objetivo

Todo texto visível do fluxo do NIR que cita um caso passa a usar a referência
humana: 6 flash messages, alerta de lote na home e página «Reenviar
corrigido» (esta e o flash de reenvio usam a forma desambiguada `nº · sufixo`).

## Contexto necessário

- `apps/intake/views.py` — flashes com uid completo: `:295` (criado),
  `:459-461` (recebimento confirmado), `:539-541` (reenvio corrigido criado),
  `:636-638` e `:642-644` (liberado do gate), `:690-692` (reenvio de
  documentos). Substituir `{case.case_id}` por `{case.display_reference}`
  (ou `{new_case.disambiguated_reference}` no `:540` — o novo caso coexiste
  com o original de mesmo nº; D3 do design).
- `templates/intake/home.html:26-31` — alerta de lote: link `Caso
  {{ created.case_id }}` → `Caso {{ created.display_reference }}` (href
  inalterado).
- `templates/intake/corrected_resubmission.html` — breadcrumb-link `:12` e
  parágrafo `<strong>` `:22` citam `case.case_id` → usar
  `case.disambiguated_reference` (página liga original ↔ novo).
- Loggers (`logger.*`), `reverse(...)`, `_gate_action_error` usam uid e
  PERMANECEM (chave técnica, não texto visível).
- Campos deferred: as views de recebimento/liberação/reenvio carregam o caso
  com `.only("pk", "case_id", "created_by_id")` (`:446`, `:623`, `:664`) — ao
  citar `display_reference` o flash leria `agency_record_number` como campo
  deferred (query implícita); incluir o campo nesses `.only()`. Criação
  (`:295`, objeto retornado pelo serviço `create_case`) e reenvio (`:540`,
  `create_corrected_resubmission`) recebem objetos completos — sem ajuste.
- Tests atuais: `test_creation.py` (flash criado + alerta home),
  `test_corrected_resubmission.py` (página + flash reenvio),
  `test_gate_actions.py` (flashes liberado/reenviado),
  `test_closure_views.py` (flash recebimento confirmado). Revisar pins que
  assertam o uid em texto de mensagem.

## Requisitos verificáveis

- **R1** — Flash de criação (`:295`): cita `display_reference` do caso criado.
- **R2** — Flashes de recebimento/liberação/reenvio (`:459`,`:636`,`:642`,`:690`):
  citam `display_reference`; uid completo ausente do texto.
- **R3** — Flash de reenvio corrigido (`:540`): cita
  `new_case.disambiguated_reference` (`N · <8 chars>`).
- **R4** — Alerta de lote (`home.html:28`): link exibe
  `Caso <display_reference>`.
- **R5** — «Reenviar corrigido» (`:12`,`:22`): exibem
  `disambiguated_reference` do caso original.
- **R6** — Loggers/URLs/redirects permanecem com uid (sem mudança).
- **R7** — Os 3 `.only()` afetados (`:446`, `:623`, `:664`) passam a incluir
  `agency_record_number` — sem query implícita de campo deferred nos flashes.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/intake/views.py` | `test_creation.py` (flash cita nº, não uid) |
| R2 | `apps/intake/views.py` | `test_gate_actions.py`, `test_closure_views.py` |
| R3 | `apps/intake/views.py` | `test_corrected_resubmission.py` (flash `N · <8>`) |
| R4 | `templates/intake/home.html` | `test_creation.py` (alerta de lote) |
| R5 | `templates/intake/corrected_resubmission.html` | `test_corrected_resubmission.py` |
| R6 | `apps/intake/views.py` | `rg -n "logger\..*case_id|reverse\(.*case_id" apps/intake/views.py` — inalterado (inspeção do diff) |
| R7 | `apps/intake/views.py` | inspeção do diff: `agency_record_number` presente nos 3 `.only()` citados |

## Plano de testes

### RED

- Comando: `uv run pytest apps/intake/tests/test_creation.py apps/intake/tests/test_corrected_resubmission.py apps/intake/tests/test_gate_actions.py apps/intake/tests/test_closure_views.py -q`
- Falha esperada: asserts novos de `display_reference`/`disambiguated_reference`
  no texto falham (mensagens atuais interpolam o uid completo).

### GREEN / verificação local

- O mesmo comando do RED — exit 0
- `uv run pytest apps/intake/tests/ -q` — sem regressão no app
- `uv run ruff check apps/intake && uv run ruff format --check apps/intake` — exit 0
- `uv run mypy apps/intake/views.py` — exit 0 (f-strings com properties tipadas)

## Critérios de aceitação

- [ ] R1-R7 verdes; nenhum `case.case_id`/`new_case.case_id` restante em
      `messages.*` (verificar com `rg -n "messages\.(success|error)" apps/intake/views.py`)
- [ ] Diff de `views.py` toca somente as 6 strings de flash + o acréscimo de
      `agency_record_number` aos 3 `.only()` (`:446`, `:623`, `:664`)
- [ ] uid permanece em loggers/reverse/redirects (R6)

## Handoff

Se algum teste existente depender do uid no texto da mensagem de forma que
não baste atualizar o pin, escalar ao parent antes de ampliar o teste.
