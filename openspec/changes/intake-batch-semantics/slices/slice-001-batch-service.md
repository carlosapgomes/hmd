# Slice 001 — Serviço do lote: `submit_report_batch` + caso único + settings

## Objetivo

O coração da nova semântica no serviço (molde ats-web): `submit_report_batch`
cria **um caso por PDF** com o tipo único do lote, falhas parciais por
arquivo, anexos só com exatamente 1 PDF e limites de lote. A primitiva
`create_case_with_documents` passa a criar **um** caso de **um** arquivo com
`procedure_type` único. Settings de lote novos; `INTAKE_MAX_DOCUMENTS`
removido.

## Contexto necessário (ler antes de editar)

- `apps/intake/services.py` — INTEIRO o bloco de criação: `_assert_intake_enabled`,
  `validate_document_batch` (:113), `validate_document_file`,
  `create_case_with_documents` (:146, assinatura `files`/`procedure_types`/
  `attachments`/`corrects_case`), o gate de regulação e `create_corrected_resubmission`
  (:479). O reenvio é o SLICE 003 — NÃO mudar aqui sua semântica, MAS a
  primitiva única (D2) é compartilhada: ajuste o reenvio para chamar a
  primitiva nova com 1 arquivo sem mudar suas regras externas (ou isole o
  mínimo necessário e reporte).
- `apps/attachments/services.py` — `validate_attachments` (fonte única):
  ganha parâmetro `pdf_count` (regra: `pdf_count != 1` → erro nomeado ANTES
  das checagens de contagem/tamanho — molde ats-web `validate_attachments`).
- `config/settings/base.py:225-243` — settings atuais (`INTAKE_MAX_DOCUMENTS`,
  `INTAKE_MAX_FILE_MB`, `ATTACHMENTS_*`).
- `/projects/dev/ats-web/apps/intake/services.py:522-599, 700-780` (SOMENTE
  LEITURA — projeto de referência): `validate_batch`,
  `validate_attachments(pdf_count)`, `process_upload` (ordem das validações
  e semântica de falhas parciais).
- `openspec/changes/intake-batch-semantics/design.md` — D1 (ordem), D2
  (primitiva), D3 (settings e o limite do Cloudflare).
- Delta specs (cenários que os testes devem espelhar):
  `specs/intake-nir/spec.md` (6 cenários) e `specs/attachments/spec.md`.

## Requisitos verificáveis

- **R1** — `submit_report_batch(*, user, role, files, procedure_type,
  attachments=()) -> tuple[list[Case], list[str]]` com a ordem D1: gate
  intake → tipo único (catálogo; inválido → `([], [erro])`) → limites de
  lote (vazio/contagem/total → `([], [erro])`) → anexos c/ `pdf_count` →
  loop por arquivo (inválido → erro com NOME do arquivo, continua) → anexos
  gravados só quando exatamente 1 caso criado e anexos válidos.
- **R2** — Primitiva de caso único: `create_case_with_documents` (nome
  mantido) cria 1 caso de 1 arquivo com `procedure_type` único; transação
  atômica preservada (caso + 1 documento + eventos + anexos opcionais).
- **R3** — Anexos×pdf_count: 2 PDFs + anexos → 2 casos SEM anexos + erro
  informativo; 1 PDF + anexo inválido → `([], [erro do anexo])` (nada
  criado); 1 PDF + anexos válidos → caso + anexos na mesma transação.
- **R4** — Settings: `INTAKE_MAX_FILES_PER_BATCH` (30),
  `INTAKE_MAX_UPLOAD_BYTES_PER_FILE` (20 MB, derivado de/replace
  `INTAKE_MAX_FILE_MB`), `INTAKE_MAX_UPLOAD_BYTES_PER_BATCH` (100 MB);
  `INTAKE_MAX_DOCUMENTS` removido SEM usos restantes (grep).
- **R5** — Limites: >30 arquivos ou >total → `([], [erro claro com o
  limite])`; arquivo individual >20 MB → erro por arquivo (não aborta o
  lote).
- **R6** — Suíte existente do intake/attachments: testes que codificavam a
  semântica antiga (N docs 1 caso, tipos múltiplos) são REESCRITOS para a
  nova (não deletados: a cobertura migra); nenhum teste fora de
  `apps/intake/tests` + `apps/attachments/tests` precisa mudar (se
  precisar, PARE e reporte).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/intake/services.py` | `test_submit_batch_creates_case_per_pdf` · `test_submit_batch_invalid_type_rejects_all` · `test_submit_batch_limits_reject_all` (novo `test_submit_report_batch.py` ou no `test_documents.py` existente) |
| R2 | `apps/intake/services.py` | `test_single_case_primitive_one_file_one_type` (reescrita do teste de criação) |
| R3 | `apps/attachments/services.py` + intake | `test_attachments_rejected_with_multi_pdf_cases_created` · `test_single_pdf_invalid_attachment_aborts` · `test_single_pdf_attachments_in_same_transaction` |
| R4 | `config/settings/base.py` | `grep -rn INTAKE_MAX_DOCUMENTS` → 0 usos; asserts de settings no teste |
| R5 | `apps/intake/services.py` | `test_batch_over_file_limit` · `test_batch_over_total_bytes` · `test_single_file_too_large_error_names_file` |
| R6 | `apps/intake/tests/*`, `apps/attachments/tests/*` | `TEST_DB_PORT=55435 uv run pytest apps/intake apps/attachments -q` verde |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/intake/services.py
  - apps/attachments/services.py
  - config/settings/base.py
  - apps/intake/tests/          # reescritas/novos (vários arquivos possíveis)
  - apps/attachments/tests/     # idem

allowed_incidental_files:
  - apps/intake/forms.py        # APENAS se a view/form quebrar na compilação de tipos (mypy) — semântica do form é o slice 002; se precisar de mais que ajuste mecânico de tipos, PARE e reporte

out_of_scope:
  - views/templates/JS (slice 002)
  - reenvio corrigido SEMÂNTICA nova (slice 003) — só o mínimo p/ compilar/chamar a primitiva
  - cluster/FSM/anonimização/LLM (N casos independentes usam o fluxo existente)
  - .env.example/compose (o parent documenta no slice 002 junto com a UI)
```

## Notas de implementação

- Mensagens de erro nomeiam o arquivo (`f"{file.name}: motivo"`) no loop
  por arquivo; erros de lote/tipo/anexos são únicos e claros (molde ats-web).
- `upload_phase="initial"` nos anexos do envio (campo já existe).
- Events/tasks por caso seguem exatamente como na criação única atual
  (a primitiva é a mesma — o lote só a chama N vezes).
- NÃO reestruture o gate de regulação (`regulation_gate`): ele roda por
  documento/caso como hoje (1 doc por caso agora).

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/intake apps/attachments -q`
- falha esperada: os testes NOVOS de lote falham (`submit_report_batch` não
  existe / cria 1 caso com N docs na versão atual); as reescritas de R6
  ainda não aplicadas mantêm a suíte antiga rodando contra código novo SOMENTE
  se você implementar antes de reescrever — ordem correta: escreva testes
  novos (RED), implemente, reescreva os antigos (GREEN final).

### GREEN

- mesmo comando — 0 failed (novos + reescritos).

### Verificação do slice

- `TEST_DB_PORT=55435 uv run pytest apps/intake apps/attachments apps/cases -q` — 0 failed
- `grep -rn INTAKE_MAX_DOCUMENTS . --include="*.py"` → 0
- `uv run ruff check apps/intake apps/attachments config && uv run ruff format --check apps/intake apps/attachments config` — ok
- `uv run mypy apps` — ok

## Critérios de aceitação

- [ ] R1–R6 verdes conforme a matriz
- [ ] RED demonstrado antes do GREEN (mesmo comando)
- [ ] Anexos×pdf_count idêntico ao ats-web (parcial no lote, aborto no único)
- [ ] Nenhum arquivo fora do blast radius
