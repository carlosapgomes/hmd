# Slice 001 — Serviços: lote + primitiva única + gate resubmit 1-PDF + reenvio corrigido + settings

## Objetivo

Toda a semântica nova no serviço (rev. 2 pós-review): `submit_report_batch`
(um caso por PDF, tipo único, falhas parciais, anexos×pdf_count, limites de
lote), primitiva `create_case_with_documents` de caso único (1 arquivo, 1
tipo), **gate resubmit com exatamente 1 PDF** (P0-1), **reenvio corrigido
com exatamente 1 PDF + tipo único + anexos** (P0-2) e settings literais de
lote (removendo `INTAKE_MAX_DOCUMENTS`/`INTAKE_MAX_FILE_MB`).

## Contexto necessário (ler antes de editar)

- `apps/intake/services.py` — INTEIRO: `_assert_intake_enabled`,
  `validate_document_batch`/`validate_document_file` (:104-123),
  `create_case_with_documents` (:146), `resubmit_case_documents` (:393-460,
  o REENVIO DO GATE — loop N docs + `_validate_batch` com
  `INTAKE_MAX_DOCUMENTS`), `create_corrected_resubmission` (:479, kwargs
  `files`/`procedure_types`).
- `apps/attachments/services.py` — `validate_attachments`: ganha
  `pdf_count` (regra `!= 1` ANTES de contagem/tamanho).
- `config/settings/base.py:225-243` — settings atuais.
- `/projects/dev/ats-web/apps/intake/services.py:522-599,700-780,821-860`
  (SOMENTE LEITURA) — molde de `validate_batch`/`validate_attachments(pdf_count)`/
  `process_upload`/`create_corrected_resubmission` ("must be a single valid PDF").
- `openspec/changes/intake-batch-semantics/design.md` rev. 2 — D1 (ordem +
  contrato de falha de persistência), D2 (TRÊS entradas), D3 (settings
  literais), D5 (reenvio).
- Deltas (cenários a espelhar): `specs/intake-nir/spec.md` (7 ADDED +
  gate MODIFIED), `specs/attachments/spec.md`, `specs/case-closure/spec.md`.
- Nota: o gate de regulação roda UMA vez por caso sobre o texto
  concatenado (`apps/intake/tasks.py:205-219`) — com 1 doc/caso vira
  genuinamente por-relatório; SEM mudança de código nele.

## Requisitos verificáveis

- **R1** — `submit_report_batch` conforme design D1 (ordem, parcial,
  anexos×pdf_count, falha de persistência por arquivo preservando casos
  anteriores).
- **R2** — Primitiva de caso único: 1 arquivo + `procedure_type` único +
  anexos opcionais, transação atômica; kwargs antigos (`files`,
  `procedure_types`) extintos.
- **R3** — `resubmit_case_documents` (gate): **exatamente 1 PDF** (0 ou >1 →
  erro nomeado, nada alterado), substitui o documento e reprocessa como
  hoje; sem dependência de `INTAKE_MAX_DOCUMENTS`.
- **R4** — `create_corrected_resubmission`: exatamente 1 PDF + tipo único
  redeclarável + anexos `upload_phase="corrected"` (anexo inválido aborta);
  molde ats-web.
- **R5** — Settings D3 (30 / 20MB literal / 100MB) + remoção de
  `INTAKE_MAX_DOCUMENTS` e `INTAKE_MAX_FILE_MB` (grep 0 usos em código).
- **R6** — Suítes migradas: intake/attachments reescritas p/ semântica
  nova; `apps/intake/tests/test_intake_lock.py` ganha `submit_report_batch`
  no inventário fail-closed; os TRÊS arquivos EXTERNOS que chamavam a
  primitiva antiga são adaptados (P1-1):
  `apps/pipeline/tests/test_orchestrator.py:500-505,543-548` e
  `apps/anonymization/tests/test_tasks.py:416-421` (tipo único + respostas
  LLM scriptadas adaptadas).
- **R7** — Testes de "casos nascem em NEW" usam
  `override_settings(INTAKE_RUN_TASKS_INLINE=False)` (inline é pinado no
  test.py — sem o override o caso avança sozinho).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/intake/services.py` | `test_submit_batch_*` (lote cria N casos; parcial; limites; tipo; anexos×pdf_count; falha de persistência simulada) |
| R2 | `apps/intake/services.py` | `test_single_case_primitive_*` |
| R3 | `apps/intake/services.py` | `test_gate_resubmit_exactly_one_pdf` (0, >1, 1 OK reprocessa) — reescrita de `test_gate_actions.py` |
| R4 | `apps/intake/services.py` | `test_resubmission_*` (reescritas de `test_corrected_resubmission.py`: 1 PDF, tipo único, anexos corrected, aborto) |
| R5 | `config/settings/base.py` | greps: `INTAKE_MAX_DOCUMENTS`/`INTAKE_MAX_FILE_MB` → 0 em apps/+config |
| R6 | 3 arquivos externos + `test_intake_lock.py` | `TEST_DB_PORT=55435 uv run pytest apps/intake apps/attachments apps/pipeline apps/anonymization -q` verde |
| R7 | testes novos | asserts de status NEW com override |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/intake/services.py
  - apps/attachments/services.py
  - config/settings/base.py
  - apps/intake/tests/            # várias reescritas/novos (incl. test_gate_actions, test_corrected_resubmission, test_intake_lock)
  - apps/attachments/tests/
  - apps/pipeline/tests/test_orchestrator.py    # P1-1: adaptação de fixtures p/ kwargs novos
  - apps/anonymization/tests/test_tasks.py      # P1-1 idem

allowed_incidental_files:
  - apps/intake/views.py          # APENAS ajuste mecânico de chamada p/ compilar (mypy) — UI é o slice 002
  - apps/intake/forms.py          # idem, apenas tipos/cleaned_data p/ compilar

out_of_scope:
  - views/templates/JS/UI/manual/README/.env (slice 002)
  - UI do reenvio/gate (slice 003)
  - cluster/FSM/anonimização/LLM/gate de regulação (fluxo existente, N casos independentes)
```

## Notas de implementação

- Erros por arquivo nomeiam o arquivo (`f"{name}: motivo"`); erros de
  lote/tipo/anexos são únicos e claros.
- O loop do lote envolve a criação de cada caso em try/except → erro por
  arquivo (contrato D1 de parcialidade inclui falha de persistência).
- **Sem `upload_phase`** (desvio aprovado na execução): o campo não existe no
HMD (change 10, "espelho enxuto sem fase") e nenhuma spec o exige — anexos do
reenvio são gravados no novo caso como os do envio; modelo/migration intocados.

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/intake apps/attachments -q`
- falha esperada: testes novos de lote/gate-1-PDF/reenvio-1-PDF falham
  (semântica atual é N docs 1 caso; gate/reenvio aceitam múltiplos).

### GREEN

- `TEST_DB_PORT=55435 uv run pytest apps/intake apps/attachments apps/pipeline apps/anonymization apps/cases -q` — 0 failed.

### Verificação do slice

- greps de settings removidos → 0 usos
- `uv run ruff check apps/intake apps/attachments config && uv run ruff format --check ...` — ok
- `uv run mypy apps` — ok

## Critérios de aceitação

- [ ] R1–R7 verdes conforme a matriz
- [ ] RED demonstrado antes do GREEN
- [ ] TRÊS entradas (envio/gate/reenvio) todas 1-PDF-por-caso
- [ ] Nenhum arquivo fora do blast radius
