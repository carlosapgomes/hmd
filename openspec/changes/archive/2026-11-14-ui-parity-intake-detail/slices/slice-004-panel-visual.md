# Slice 004 — Painel no visual

```yaml
expected_files:
  - templates/dashboard/*.html (toolbar de período, cards, lista em case-card)
  - static/css/app.css (só blocos de métricas, se ausentes)
  - apps/dashboard/tests/test_*visual*.py (novos, escopo estreito: valores renderizados, links de período, preservação de filtros)
  - .pi/skills/verify-hmd/{features,scripts}/ (jornada painel-visual)
  - PROJECT_CONTEXT.md (fecho do change)
allowed_incidental_files: []
out_of_scope:
  - queries de métricas, filtros, fontes imutáveis (intactos — paridade funcional é change futuro)
  - lista de casos do NIR/médico, encerramento administrativo (comportamento intacto)
  - upload, combobox, detalhe/decisão
```

## Objetivo

Painel com a apresentação do ats-web e as métricas e filtros atuais
do HMD.

## Read first

- `AGENTS.md`, `PROJECT_CONTEXT.md`
- `openspec/changes/ui-parity-intake-detail/proposal.md`, `design.md` (D4–D6)
- `templates/dashboard/` + `apps/dashboard/views.py` + `metrics.py` (leitura: o que NÃO pode mudar)
- Molde (somente leitura): `/projects/dev/ats-web/templates/dashboard/index.html` (toolbar de período, cards)

## Contexto necessário

- Métricas por período (hoje/7d/30d/tudo) em fontes imutáveis e a
  lista com identificação completa são contrato vigente — o slice
  só troca classes e estrutura.
- Trilha de eventos e encerramento administrativo preservados.

## Requisitos verificáveis

- **R1** — Toolbar de período, cards de métrica e lista em
  `case-card` com os mesmos números e filtros de antes.
- **R2** — Nenhuma query ou regra de contagem muda (diff de SQL/
  resultado zero; testes de métricas passam sem alteração).
- **R3** — Mapa verify-hmd completo nas telas do change;
  `PROJECT_CONTEXT.md` atualizado para o archive.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | templates + CSS | testes novos de conteúdo/marcadores + capturas |
| R2b | `test_*visual*.py` | valores renderizados = métricas; links de período e filtros preservados |
| R2 | — (nenhum .py de métrica) | testes de métricas existentes verdes |
| R3 | mapas + `PROJECT_CONTEXT.md` | evidência do harness + gate |

## Plano de testes

### RED

- Comando: `uv run pytest apps/dashboard -q` (testes novos de marcadores antes)
- Falha esperada: toolbar/cards novos ausentes.

### GREEN / verificação local

- `uv run pytest -q`, `mypy`, `ruff check/format` — exit 0
- jornada + capturas vs ats-web

## Critérios de aceitação

- [ ] R1–R3 verdes; números do painel idênticos antes/depois
  (conferir via testes de métricas)
- [ ] Gate completo verde + evidência do harness anexada ao relatório

## Relatório de evidência

`/tmp/hmd-slice-i004-report.md` (requirements→evidence, arquivos,
failing-before/passing-after, comandos com exit status, capturas).

## Handoff

Implementar apenas este slice. Escalar se qualquer número do painel
divergir (indica mudança acidental de query). Ao concluir, o change 3
está pronto para revisão final (não arquivar — archive é papel do
parent).
