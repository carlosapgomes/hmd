# Slice 001: App attachments — model + upload no intake + listagem NIR

## Objetivo

Anexos existem: `apps/attachments` com `CaseAttachment` (migration 0001 do
app); o NIR anexa jpg/png/pdf na criação do caso (e no reenvio corrigido)
via kwargs aditivos; o detalhe do NIR lista os anexos com status.

## Contexto necessário

- Design D1/D2 (`openspec/changes/attachment-processing-ocr/design.md`) —
  campos da row exatos; kwargs aditivos; limites D7.
- `apps/intake/services.py::create_case_with_documents` (padrão de atomic +
  gravação compensatória de arquivos `_delete_saved_files_best_effort`;
  kwargs aditivos têm precedente no change 09) e
  `create_corrected_resubmission` (repassa kwargs).
- `apps/intake/services.py::_validate_batch`/`_validate_document_file` —
  padrão de validação nomeada antes de qualquer escrita (a validação de
  ANEXOS é fonte única nova em `apps/attachments/services.py`, separada dos
  PDFs do relatório).
- Upload path seguro: padrão `case_document_upload_path`
  (`apps/cases/models.py`) → `case_attachments/<case_id>/<attachment_id>.<ext>`.
- `templates/intake/case_detail.html` (bloco de documentos existente —
  espelhar para anexos) e `templates/intake/upload`-equivalente do form de
  criação (input de anexos adicional, `multiple`).
- Settings: padrão `INTAKE_RUN_TASKS_INLINE`/`ALT_CLUSTERS` (D7); app nova
  em `INSTALLED_APPS` (precedente `apps.scheduler` do change 08).
- Tests storage: fixture `InMemoryStorage` autouse
  (`apps/intake/tests/conftest.py`) — os testes de anexo no intake já a
  herdam; se escrever testes em `apps/attachments/tests/`, replique o
  fixture lá.

## Requisitos verificáveis

- **R1** App `apps/attachments` registrado (apps.py `AttachmentsConfig`,
  `INSTALLED_APPS`); `CaseAttachment` com os campos de D1 (FK
  `case.related_name="attachments"` PROTECT; `file`; identificadores;
  `status` choices `pending|processing|processed|failed` default pending;
  campos de processamento/verificação todos vazios/null); migration
  `0001_initial` sem drift (`makemigrations --check`).
- **R2** `validate_attachments(files)` em `apps/attachments/services.py`:
  contagem ≤ `ATTACHMENTS_MAX_COUNT`, tamanho ≤ `ATTACHMENTS_MAX_SIZE_MB` por
  arquivo, MIME ∈ {image/jpeg, image/png, application/pdf}; erros nomeados
  (`AttachmentValidationError`-style ValueError) SEM efeito.
- **R3** `create_case_with_documents(..., attachments=())` — kwargs aditivo:
  no MESMO atomic da criação, grava as rows+arquivos (upload path D1,
  `uploaded_by=user`, `status=pending`); anexo inválido rejeita TUDO antes de
  qualquer gravação (nem caso, nem documentos); default `()` preserva o
  change 04 (regressão testada). `create_corrected_resubmission` ganha e
  repassa `attachments=()` (idem aditivo, regressão testada).
- **R4** UI: form de criação do intake aceita anexos (input múltiplo, em
  adição aos PDFs do relatório); detalhe do NIR lista anexos
  (nome/tamanho/MIME + status legível `get_status_display`); sem anexos →
  bloco ausente. Guards existentes intocados.
- **R5** Testes: serviço (rows+arquivos no atomic; rejeição nomeada sem
  efeito ×3: tipo/tamanho/contagem; regressão create puro sem attachments;
  resubmission com attachments e sem); views (upload com anexos via UI cria
  tudo; detalhe lista com status; sem anexos sem bloco).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/attachments/{models,apps}.py`, `apps/attachments/migrations/0001_initial.py`, `config/settings/base.py` | `test_attachment_fields`, `makemigrations --check` |
| R2 | `apps/attachments/services.py` | `test_validate_attachments_*` (tipo/tamanho/contagem) |
| R3 | `apps/intake/services.py` | `test_create_case_with_attachments`, `test_invalid_attachment_no_case_created`, `test_create_without_attachments_unchanged`, `test_resubmission_with_attachments` |
| R4 | `apps/intake/{views,forms}.py`, `templates/intake/{upload form,case_detail}.html` | `test_upload_ui_with_attachments`, `test_detail_lists_attachments_status` |
| R5 | `apps/attachments/tests/`, `apps/intake/tests/` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/attachments/__init__.py
  - apps/attachments/apps.py
  - apps/attachments/models.py
  - apps/attachments/migrations/0001_initial.py
  - apps/attachments/services.py            # validate_attachments
  - apps/attachments/tests/__init__.py
  - apps/attachments/tests/test_upload.py
  - apps/intake/services.py                 # kwargs aditivo + resubmission
  - apps/intake/views.py                    # contexto de anexos no detalhe
  - apps/intake/forms.py                    # input de anexos
  - apps/intake/tests/test_attachments_upload.py
  - templates/intake/case_detail.html       # bloco Anexos
  - templates/intake/<form de criação>.html # input múltiplo
  - config/settings/base.py                 # INSTALLED_APPS + limites D7

out_of_scope:
  - extração/worker/trigger (slice 002); verificação (slice 003)
  - cards do doctor (slice 004); closure (slice 005)
  - upload suplementar pós-criação; supressão de anexo
  - apps/cases (nenhuma mudança)
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/attachments/tests/test_upload.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.attachments'`.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/attachments/tests/
  apps/intake/tests/` — exit 0 (regressão do intake).
- `uv run ruff check apps/attachments apps/intake config &&
  uv run ruff format --check apps/attachments apps/intake config`
- `uv run mypy .`
- `uv run python manage.py makemigrations --check --dry-run`

## Critérios de aceitação

- [ ] R1–R5 comprovados; anexo inválido jamais cria caso (validação antes de
      tudo)
- [ ] Regressão do change 04 (sem attachments) e do 09 (resubmission)
      cobertas
- [ ] Upload path seguro (case_id/attachment_id); sem anexos → UI idêntica
- [ ] Gate parcial do slice verde
