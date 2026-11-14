# Slice 003 — Meus casos com filtros, cards e auto-refresh

```yaml
expected_files:
  - apps/intake/views.py (filtros q/status/procedure_type + partial)
  - apps/intake/urls.py (nova rota `cases/partials/list/` nomeada `my_cases_partial`)
  - templates/intake/my_cases.html (cards + filtros + abas + htmx)
  - templates/intake/_my_cases_content.html (novo, fragmento compartilhado)
  - static/js/nir_queue_filter.js (novo, só reaplicação pós-swap; busca textual é server)
  - apps/intake/tests/test_my_cases*.py (novos/estendidos, tipados)
  - .pi/skills/verify-hmd/{features,scripts}/ (jornada meus-casos-filtros)
allowed_incidental_files: []
out_of_scope:
  - detalhe do caso, upload, reenvio corrigido
  - mudança de escopo (criador) ou das abas Ativos/Encerrados
  - painel e filas médicas/agendador
```

## Objetivo

Meus casos no padrão ats-web: cards com espera, filtros que compõem,
abas com contadores e atualização automática — escopo por criador
intacto.

## Read first

- `AGENTS.md`, `PROJECT_CONTEXT.md`
- `openspec/changes/ui-parity-queues/proposal.md`, `design.md` (D1–D2, D4–D6), `specs/intake-nir/spec.md`
- `templates/intake/my_cases.html`, `templates/intake/_nav.html` (change 1, mantido), `apps/intake/views.py`, `apps/intake/urls.py`
- Molde (somente leitura): `/projects/dev/ats-web/templates/intake/my_cases.html`, `/projects/dev/ats-web/templates/intake/_my_cases_content.html`

## Contexto necessário

- `templates/intake/my_cases.html` (list-group, abas
  Ativos/Encerrados com contadores) e a view correspondente
  (escopo por criador, sem paginação — lista materializada).
- Molde de filtros: `/projects/dev/ats-web/templates/intake/
  my_cases.html` (q + status + exam_type, AND, preservados) e
  partial com htmx.
- Catálogo HMD de 13 perfis para o filtro de tipo (values já usados
  no `IntakeUploadForm.procedure_type`).
- Decisões do design: D1, D2 (sem paginação nesta view), D4
  (`q` ≥3 chars, AND, preservados entre página e partial; filtros
  aplicam-se antes dos sufixos, como as abas), D5.

## Requisitos verificáveis

- **R1** — Cards com nome+idade, nº, status, procedimentos e retenção
  (quando houver); as pills `Ativos/Encerrados` (com contadores)
  permanecem como navegação das abas — reestilizadas, não removidas
  (diferente das filas, aqui não há duplicação com o `_nav`, que é a
  navegação da seção).
- **R2** — Filtros `q` (nº, ≥3 chars; mais curtos ignorados),
  `status`, `procedure_type` com AND, preservados entre página cheia
  e partial (sem paginação nesta view); só casos do criador em
  qualquer combinação; caso fora do filtro não aparece no refresh.
- **R3** — Partial com auto-refresh 20 s preservando aba e filtros.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `my_cases.html` | testes de marcadores por aba |
| R2 | `views.py`, templates | testes de query (AND, q curto, criador cruzado → vazio) |
| R3 | partial, urls | teste do fragmento + jornada harness |

## Plano de testes

### RED

- Comando: `uv run pytest apps/intake/tests/ -q` (testes novos antes)
- Falha esperada: filtros/partial/marcadores ausentes.

### GREEN / verificação local

- `uv run pytest apps/intake -q`, `mypy`, `ruff check/format` — exit 0
- jornada verify-hmd: filtra; novo caso compatível aparece sem reload (caso fora do filtro não aparece)

## Critérios de aceitação

- [ ] R1–R3 verdes; NIR nunca vê caso alheio em nenhuma combinação
  de filtro/aba
- [ ] `q` com <3 chars ignorado (não quebra nem filtra tudo para zero
  por erro)

## Relatório de evidência

`/tmp/hmd-slice-q003-report.md` (mesmo formato dos slices 001-002).

## Handoff

Implementar apenas este slice. Escalar se algum filtro exigir índice
novo ou query fora dos guards de criador. Ao concluir, o change 2
está pronto para revisão final (não arquivar — archive é papel do
parent).
