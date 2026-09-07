# Slice 001: CaseDocument + criação atômica com declaração de tipos

## Objetivo

O caso nasce: model `CaseDocument` (multi-PDF ordenado), campos novos no `Case` (`extracted_text`, `manual_review_required`, `manual_review_reason`), serviço `create_case_with_documents` (validação → transação única com declaração) e a tela de upload do NIR com multi-select dos 13 tipos. Comportamento observável: NIR autenticado cria caso em `NEW` com N PDFs e tipos declarados; arquivos inválidos rejeitam tudo.

## Contexto necessário (contexto zero)

- Change 03 (arquivado) entregou `apps/cases`: `Case` com FSM (transição `start_pdf_extraction` disponível a partir de `NEW`), `agency_record_number`+`agency_record_extracted_at`, `set_declared_procedures(case, types, *, user, role)` (valida catálogo, atômico, grava evento), `procedure_catalog` (13 tipos com labels), `@role_required` em `apps/accounts/decorators.py`, papel ativo na sessão (`request.session["active_role"]`, context processor expõe).
- Referência (somente-leitura): `/projects/dev/ats-web/apps/intake/{services,forms,views}.py` (padrões de validação de upload e form de intake — o ats-web é single-PDF: `Case.pdf_file`; HMD diverge para multi, ver design D1). Validação de arquivo do ats-web (referência): `validate_single_file`/`validate_batch` em `apps/intake/services.py` (relatório principal; **atenção**: `validate_attachment_file` lá é para ANEXOS e aceita PDF/JPEG/PNG — HMD implementa validação PRÓPRIA PDF-only p/ documentos do relatório).
- Design: `../design.md` D1 (CaseDocument), D6 (authz/form), D7 (serviço atômico), D9 (campos novos chegam agora).
- Spec: `../specs/intake-nir/spec.md` — Requirement "Criação de caso com upload multi-PDF e declaração de tipos" (3 cenários).
- App `apps/intake` não existe — este slice a cria. URLs sob `/intake/` (home do app; a raiz `/` continua home placeholder por enquanto).

## Requisitos

- **R1** `CaseDocument`: `case FK PROTECT related_name="documents"`, `file FileField(upload_to="case_documents/<case_id>/")` (path com UUID e ext `.pdf`; storage seguro), `position PositiveSmallIntegerField`, `original_filename`, `content_type`, `size_bytes`, `uploaded_by FK`, `created_at`; `UniqueConstraint(case, position)`; ordering por `position`. Migration.
- **R2** `Case`: +`extracted_text TextField blank`, `manual_review_required bool default False`, `manual_review_reason CharField blank`. Migration incremental.
- **R3** `create_case_with_documents(*, user, role, files, procedure_types)`: valida ANTES de persistir — cada file: content-type `application/pdf` (e extensão), tamanho ≤ `INTAKE_MAX_FILE_MB` (default 20), contagem 1..`INTAKE_MAX_DOCUMENTS` (default 10); tipos: ≥1 e todos no catálogo (erro nomeia arquivo/tipo inválido). Sucesso: transação única com `Case(NEW, created_by=user)` + `CaseDocument` rows (position 1..N) + `set_declared_procedures(...)`. Não enfileira task ainda (slice 003 liga). Em exceção após gravar arquivos físicos: limpeza compensatória best-effort (unlink) — rollback do banco não reverte filesystem (design D7).
- **R4** `IntakeUploadForm`: campo arquivo múltiplo + campo tipos (checkboxes a partir do `procedure_catalog`, labels do catálogo); view `intake_home` GET renderiza, POST chama o serviço e redireciona ao detalhe (404-safe) com mensagem de sucesso; erros de validação re-renderizam com resumo nomeando arquivo/tipo.
- **R5** View e urls protegidas por `@role_required("nir")`; templates estendem `base.html` (tema HMD existente); `config/urls.py` inclui `apps.intake.urls`.
- **R6** Testes (client Django, papel ativo nir): criação com 2 PDFs + 2 tipos → caso `NEW`, 2 docs ordenados, 2 rows declaradas, evento `CASE_PROCEDURES_DECLARED`; não-PDF no lote → nada persiste + erro nomeia o arquivo; acima do limite de count → idem; sem tipo declarado → erro; papel não-nir → 403.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/cases/models.py`, `apps/cases/migrations/000X_casedocument.py` | `test_creation.py::test_documents_ordered_and_unique` |
| R2 | `apps/cases/models.py`, migration | `test_creation.py::test_case_new_fields_defaults` |
| R3 | `apps/intake/services.py` | `::test_create_atomic_success`, `::test_non_pdf_rejects_all`, `::test_over_count_limit_rejects`, `::test_requires_at_least_one_type` |
| R4 | `apps/intake/{forms,views}.py`, `templates/intake/home.html` | `::test_upload_flow_via_view` |
| R5 | `apps/intake/{views,urls}.py`, `config/urls.py` | `::test_non_nir_role_forbidden` |
| R6 | `apps/intake/tests/test_creation.py` | `uv run pytest apps/intake/tests/test_creation.py` |

## RED

- Comando: `uv run pytest apps/intake/tests/test_creation.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.intake'` — intake não existe.

## GREEN / verificação local

- `uv run pytest apps/intake/tests/test_creation.py` — exit 0
- `uv run pytest apps/intake/tests/ apps/cases/tests/` — exit 0 (regressão cases)
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0
- `uv run python manage.py makemigrations --check --dry-run` — sem drift

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/intake/{__init__,apps,urls,forms,views,services}.py
  - apps/intake/tests/{__init__,test_creation}.py
  - templates/intake/home.html
  - apps/cases/models.py            # + CaseDocument + campos Case
  - apps/cases/migrations/000X_*.py
  - config/settings/base.py         # INTAKE_MAX_DOCUMENTS / INTAKE_MAX_FILE_MB + INSTALLED_APPS
  - config/urls.py
  - .env.example
allowed_incidental_files:
  - apps/intake/tests/conftest.py (fixtures: nir_user, pdf factory simples)
out_of_scope:
  - task/worker/q2/extração (slice 003) — criação NÃO processa nada além de persistir
  - pdf_utils/regulation_gate (slice 002)
  - meus casos/detalhe (slice 004) — o redirect do POST pode ir p/ home por ora, ajuste no 004
  - serve de PDF (004)
```

Escale ao parent se: o storage seguro exigir dependência; o form precisar de JS além do Bootstrap nativo.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz (3 cenários da spec cobertos)
- [ ] Toda validação antes de qualquer persistência (falha = zero efeito)
- [ ] Documentos ordenados com constraint única por posição
- [ ] Gate parcial do slice verde
