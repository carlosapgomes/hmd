# Slice 002 — Form/UI: tipo único, hints numéricos, anexos desabilitáveis, resultado do lote

## Objetivo

A tela de envio reflete a semântica do lote: `procedure_type` único (radio),
hints com os números reais dos settings (cada PDF = um relatório = um caso;
limites), campo de anexos **desabilitado via JS** quando >1 arquivo
selecionado, e resultado do envio listando casos criados + erros por arquivo.
`.env.example`/compose documentam os settings novos.

## Contexto necessário (ler antes de editar)

- `apps/intake/forms.py` — `IntakeUploadForm` (documents/attachments/
  `procedure_types` MultipleChoiceField — vira `procedure_type`
  ChoiceField radio) e `CorrectedResubmissionForm` (herda).
- `apps/intake/views.py:~200-230` — fluxo do POST: chama o serviço do slice
  001; passa a consumir `(cases, errors)` e renderizar o resultado.
- `templates/intake/home.html` — form + nova seção de resultado (N casos
  criados, erros por arquivo com nome) + registro do JS.
- `static/js/` — padrão do sw.js; novo `static/js/intake-upload.js`
  (vanilla, sem framework).
- `config/settings/base.py` — settings do slice 001 (números dos hints).
- `.env.example` / `docker-compose.prod.yml` — documentar
  `INTAKE_MAX_FILES_PER_BATCH`, `INTAKE_MAX_UPLOAD_BYTES_PER_FILE`,
  `INTAKE_MAX_UPLOAD_BYTES_PER_BATCH` (defaults 30/20MB/100MB — nota do
  limite do Cloudflare ~100MB no comentário do .env.example); remover
  `INTAKE_MAX_DOCUMENTS` se lá constar.
- `openspec/changes/intake-batch-semantics/design.md` — D3/D4.

## Requisitos verificáveis

- **R1** — `procedure_type = ChoiceField` único (choices do catálogo, radio)
  com help "um tipo por envio, aplicado a todos os relatórios do lote";
  submissão com tipo ausente/inválido → erro de formulário, sem chamar o
  serviço.
- **R2** — Hints numéricos dinâmicos: documents help informa limite de
  arquivos, tamanho por arquivo e total (dos settings) + "cada PDF é um
  relatório de um paciente e vira um caso"; attachments help acrescenta
  "somente quando o envio tiver exatamente 1 relatório".
- **R3** — JS: selecionar >1 arquivo desabilita o input de anexos
  (atributo `disabled` + classe visual) e mostra hint "anexos só com
  exatamente 1 relatório"; com 1 arquivo reabilita/oculta o hint. Sem
  submissão involuntária; teclado acessível (label/aria).
- **R4** — Resultado do lote: após POST bem-sucedido com N casos e/ou
  erros, a página exibe "N casos criados" (link p/ Meus casos) e a lista de
  erros por arquivo (nome + motivo) quando houver.
- **R5** — `.env.example` + compose repassam os 3 settings novos com
  defaults; sem referências a `INTAKE_MAX_DOCUMENTS` restantes (grep).
- **R6** — Testes de form/view: radio único renderiza; hints contêm os
  números; attrs do input de anexos; POST de lote renderiza resultado com
  contagem e erros; testes antigos de form migrados para o campo único.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/intake/forms.py` | `test_form_single_procedure_type_radio` + `test_form_requires_type` |
| R2 | `apps/intake/forms.py` | asserts de help_text com os valores dos settings |
| R3 | `static/js/intake-upload.js`, `templates/intake/home.html` | teste de template: script registrado + id/attrs que o JS usa; (JS em si é coberto por attrs/estrutura — sem framework de browser tests no repo) |
| R4 | `apps/intake/views.py`, `templates/intake/home.html` | `test_upload_batch_result_shows_created_and_errors` |
| R5 | `.env.example`, `docker-compose.prod.yml` | `grep INTAKE_MAX_FILES_PER_BATCH` em ambos; `grep INTAKE_MAX_DOCUMENTS` → 0 |
| R6 | testes | suíte `apps/intake` verde |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/intake/forms.py
  - apps/intake/views.py
  - templates/intake/home.html
  - static/js/intake-upload.js
  - .env.example
  - docker-compose.prod.yml
  - apps/intake/tests/          # form/view tests migrados/novos

allowed_incidental_files:
  - apps/accounts/tests/test_manual.py   # APENAS se o manual citar tipos múltiplos no envio — atualizar frase; se exigir mais, PARE e reporte

out_of_scope:
  - serviços (slice 001 encerrado), reenvio (slice 003), cluster/FSM
  - PWA/sw.js (o JS novo não precisa de cache especial — não registrar no SW)
```

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/intake -q`
- falha esperada: testes novos de form único/resultado falham (form atual é
  checkboxes múltiplos; view não renderiza resultado de lote).

### GREEN

- mesmo comando — 0 failed.

### Verificação do slice

- `TEST_DB_PORT=55435 uv run pytest apps/intake apps/attachments apps/accounts -q` — 0 failed
- `grep -rn INTAKE_MAX_DOCUMENTS .env.example docker-compose.prod.yml apps/ config/` → 0
- `docker compose -f docker-compose.prod.yml --env-file .env.example config --quiet` (dummies de secret `_FILE` se exigidas)
- `uv run ruff check apps/intake && uv run ruff format --check apps/intake` — ok; `uv run mypy apps` — ok

## Critérios de aceitação

- [ ] R1–R6 verdes conforme a matriz
- [ ] RED demonstrado antes do GREEN (mesmo comando)
- [ ] Hints com números dinâmicos dos settings (não hardcoded)
- [ ] Nenhum arquivo fora do blast radius
