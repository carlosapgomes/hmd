# Slice 003 — UI do reenvio corrigido e do gate (hints/templates)

## Objetivo

A SUPERFÍCIE do reenvio corrigido e do reenvio do gate alinha à semântica
nova (serviço já pronto no slice 001 — rev. 2: semântica migrou p/ o slice
001 por P0-2): templates/hints coerentes com "exatamente 1 PDF + tipo
único + anexos", campo de documentos do gate sem `multiple`, e testes de
view/template.

## Contexto necessário (ler antes de editar)

- `apps/intake/services.py` — `create_corrected_resubmission` (:~479):
  hoje aceita `files` múltiplos + `procedure_types`; passa a exigir 1
  arquivo + `procedure_type` único, usando a primitiva do slice 001.
- `apps/intake/views.py:~380-440` — fluxo do reenvio (view do caso
  recusado): form `CorrectedResubmissionForm` (herda o form único do slice
  002 — conferir se o campo de documentos do reenvio precisa de hint
  próprio "exatamente 1 PDF").
- `templates/intake/corrected_resubmission.html` — hints do reenvio.
- `/projects/dev/ats-web/apps/intake/services.py:821-860` (SOMENTE LEITURA):
  `create_corrected_resubmission` do ats-web ("must be a single valid PDF").
- `openspec/changes/intake-batch-semantics/design.md` — D5.
- Specs: o reenvio é citado na spec `attachments` MODIFIED ("no reenvio
  corrigido") e nos requisitos de case-closure — sem delta novo de spec
  (o comportamento já está coberto pelos ADDED/MODIFIED deste change).

## Requisitos verificáveis

- **R1** — Hints do CAMPO no reenvio: `CorrectedResubmissionForm` sobrescreve o
  help_text de `documents` (herdado do lote) para "exatamente 1 PDF do
  relatório corrigido" (P2 rastreado da review do slice 002 — sem isso a
  página do reenvio mostra o hint de LOTE). Views do reenvio corrigido (`corrected_resubmission.html` +
  fluxo da view) renderizam hints novos: "exatamente 1 PDF do relatório
  corrigido", tipo único (herdado do form do slice 002), anexos permitidos;
  erros nomeados do serviço são exibidos.
- **R2** — UI do REENVIO DO GATE (`templates/intake/case_detail.html`,
  seção retida): input sem `multiple`, hint "exatamente 1 PDF" substitui o
  texto de ordem/na composição (slice 002 cuida do caso geral; aqui é o
  bloco específico do gate se não tiver sido coberto lá — confira e não
  duplique).
- **R3** — UI do reenvio coerente: form herda tipo único; hint do campo de
  documentos diz "exatamente 1 PDF do relatório corrigido"; campo de anexos
  segue normal (reenvio é sempre 1 paciente).
- **R4** — Testes: 1 PDF + anexos OK; 2 PDFs → erro nomeado; anexo inválido
  → aborta; tipo corrigido persiste; fluxo da view renderiza/valida.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/intake/services.py` | `test_resubmission_requires_exactly_one_pdf` (0 e >1) · `test_resubmission_corrects_type` |
| R2 | `apps/intake/services.py`, `apps/attachments/services.py` | `test_resubmission_attachments_corrected_phase` · `test_resubmission_invalid_attachment_aborts` |
| R3 | `apps/intake/forms.py`, `templates/intake/corrected_resubmission.html` | asserts de help_text/template |
| R4 | `apps/intake/tests/` | suíte do intake verde |

## Escopo e expected blast radius

```yaml
expected_files:
  - templates/accounts/manual.html       # P2 review slice 002: 'tipos declarados novamente'/'reenviar os documentos' → singular/exatamente 1 PDF
  - apps/intake/services.py
  - apps/intake/views.py          # apenas o fluxo do reenvio, se necessário
  - apps/intake/forms.py          # apenas hint/herança do reenvio
  - templates/intake/corrected_resubmission.html
  - apps/intake/tests/

allowed_incidental_files: []

out_of_scope:
  - envio inicial (slices 001/002 encerrados), cluster/FSM, dashboard
  - novos deltas de spec (cobertos pelos ADDED/MODIFIED do change)
```

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/intake -q`
- falha esperada: testes novos de "exatamente 1 PDF"/anexos-corrected
  falham (reenvio atual aceita N arquivos e tipos múltiplos).

### GREEN

- mesmo comando — 0 failed (novos + migrados).

### Verificação do slice

- `TEST_DB_PORT=55435 uv run pytest apps/intake apps/attachments apps/cases -q` — 0 failed
- `uv run ruff check apps/intake && uv run ruff format --check apps/intake` — ok
- `uv run mypy apps` — ok

## Critérios de aceitação

- [ ] R1–R4 verdes conforme a matriz
- [ ] RED demonstrado antes do GREEN (mesmo comando)
- [ ] Nenhum arquivo fora do blast radius
