# Slice 004: Meus casos e detalhe do NIR

## Objetivo

Visibilidade do NIR sobre seus casos: lista "meus casos" (escopo por criador, status, tipos declarados, indicador de retenção do gate) e detalhe do caso (documentos com visualização segura do PDF, trilha de eventos e comunicações). Acesso a caso alheio é 404.

## Contexto necessário (contexto zero)

- Slices 001–003 entregues: criação + task de processamento (casos chegam a `ANONYMIZING`, retidos em `PDF_EXTRACTING` com flag, ou `FAILED`).
- Change 03: `CaseEvent` (ordering timestamp,id), `CaseCommunicationMessage` (thread por caso), `CaseStatus` (labels), `procedure_catalog.get_procedure_profile(type).label`, `@role_required("nir")`.
- Referência (somente-leitura): `/projects/dev/ats-web/apps/intake/views.py` (`my_cases`, `case_detail`, `serve_pdf` — entrega segura de arquivo por UUID sem expor path) e `templates/intake/*` (padrões de lista/badges — adaptar identidade HMD).
- Design: `../design.md` D6 (authz/escopo por criador; serve_document seguro), D9 (acesso só NIR criador neste change). Spec: Requirement "Meus casos e detalhe do NIR" (3 cenários).

## Requisitos

- **R1** `my_cases` (`GET /intake/cases/`): queryset `created_by=request.user` ordenado por `created_at desc`; cada item mostra nº ocorrência (ou "—"), status com label legível, tipos declarados (labels do catálogo), badge de retenção (`manual_review_required`) e badge `FAILED`; sem filtros avançados (change 11).
- **R2** `case_detail` (`GET /intake/cases/<uuid>/`): somente casos do criador (alheio → **404**); exibe status/timestamps, tipos declarados, flag de retenção com motivo, documentos (nome original, tamanho, botão abrir), trilha de eventos (tipo, ator+papel, timestamp, payload resumido) e thread de comunicações (messages do change 03).
- **R3** `serve_document` (`GET /intake/cases/<uuid>/documents/<id>/`): serve o PDF do storage (FileResponse, content-type `application/pdf`, filename original); caso alheio ou documento de outro caso → 404; sem path traversal (id interno, nunca caminho do cliente).
- **R4** Templates estendem `base.html` (tema HMD; Bootstrap já presente); links de navegação na navbar quando papel ativo é `nir` (usa context processor existente).
- **R5** O redirect do POST de criação (slice 001) passa a apontar para o detalhe do caso criado.
- **R6** Testes: lista mostra só casos do criador (2 NIRs) com badges corretos; detalhe do próprio caso renderiza documentos/trilha/comunicações; detalhe de caso alheio → 404; serve_document do próprio caso responde 200 com content-type pdf; serve_document de caso alheio → 404; não-nir → 403 em todas as rotas.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/intake/views.py`, `templates/intake/my_cases.html` | `test_my_cases.py::test_list_scoped_to_creator`, `::test_retention_and_failed_badges` |
| R2 | `apps/intake/views.py`, `templates/intake/case_detail.html` | `::test_detail_shows_docs_events_communications`, `::test_detail_foreign_case_404` |
| R3 | `apps/intake/views.py` | `::test_serve_document_ok`, `::test_serve_document_foreign_404` |
| R4 | `templates/base.html`, templates intake | `::test_navbar_links_for_nir` |
| R5 | `apps/intake/views.py` | `::test_create_redirects_to_detail` |
| R6 | `apps/intake/tests/test_my_cases.py` | `uv run pytest apps/intake/tests/test_my_cases.py` |

## RED

- Comando: `uv run pytest apps/intake/tests/test_my_cases.py`
- Falha esperada: 404/`NoReverseMatch` — rotas de meus casos/detalhe não existem.

## GREEN / verificação local

- `uv run pytest apps/intake/tests/test_my_cases.py` — exit 0
- `uv run pytest apps/intake/tests/` — exit 0
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/intake/{views,urls}.py
  - apps/intake/tests/test_my_cases.py
  - templates/intake/{my_cases,case_detail}.html
  - templates/base.html            # links navbar p/ nir
allowed_incidental_files: []
out_of_scope:
  - ações do gate (liberar/reenviar — slice 005; botões chegam lá)
  - comunicação escrita pelo NIR (post de mensagem — mudança futura se pedida)
  - busca/filtros/paginação avançada (11)
  - acesso de doctor/manager aos documentos (07+)
```

Escale ao parent se: a entrega do arquivo exigir servidor de media configurado além do FileResponse; templates exigirem JS além do Bootstrap.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz (3 cenários da spec cobertos)
- [ ] Escopo por criador garantido em lista, detalhe E serve_document (404 sem vazamento)
- [ ] Trilha e comunicações visíveis no detalhe
- [ ] Gate parcial do slice verde
