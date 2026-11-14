# Slice 003 — Detalhe, decisão e confirmação no visual

```yaml
expected_files:
  - templates/doctor/case_detail.html + decide.html
  - templates/scheduler/case_detail.html (forms embutidos de confirmação/negação/intercorrência: lock tokens, heartbeat, leave, validações preservados)
  - templates/intake/case_detail.html
  - static/css/app.css (só blocos de relatório/decisão, se ausentes)
  - apps/doctor/tests/test_*visual*.py + apps/scheduler/tests/test_*visual*.py + apps/intake/tests/test_*visual*.py (novos, escopo estreito: conteúdo/marcadores)
  - .pi/skills/verify-hmd/{features,scripts}/ (jornada detalhe-papeis)
allowed_incidental_files: []
out_of_scope:
  - formulários, validações, atomics, guards, FSM, ordem clínica dos cards (change doctor-detail-context)
  - qualquer comportamento de decision.js (decisão do dono, Q6)
  - upload, combobox, painel, filas
```

## Objetivo

Detalhe do caso (3 papéis), página de trabalho/decisão e
confirmação/negação no layout ats-web, com conteúdo e comportamento
idênticos.

## Read first

- `AGENTS.md`, `PROJECT_CONTEXT.md`
- `openspec/changes/ui-parity-intake-detail/proposal.md`, `design.md` (D3, D5–D6)
- `templates/doctor/{case_detail,decide,_case_cards}.html`, `templates/scheduler/case_detail.html` (forms embutidos), `templates/intake/case_detail.html`
- Molde (somente leitura): `/projects/dev/ats-web/templates/doctor/decision.html` (layout, não comportamento)

## Contexto necessário

- A ordem dos cards clínicos do detalhe médico é contrato do change
  `doctor-detail-context` (Procedimentos do caso → Sumário clínico →
  Estrutura extraída → Prior-case → Documentos → Anexos →
  Requisitos → Recomendação agregada → Alertas consultivos;
  «Decisões registradas» só no detalhe read-only) e NÃO muda.
- Decisão por procedimento atômica e reservas (locks) intactas; só
  classes e estrutura visual mudam.

## Requisitos verificáveis

- **R1** — Identificação, procedimentos, sumário, estrutura,
  documentos/anexos, alertas e decisões registradas renderizam o
  mesmo conteúdo no novo layout, por papel.
- **R2** — POSTs, validações, redirects e guards idênticos (diff de
  comportamento zero; suíte existente passa sem alteração).
- **R3** — Jornada completa médico→agendador→NIR funciona no novo
  visual (harness).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | templates listados | testes novos de conteúdo/marcadores + capturas vs ats-web |
| R2 | — (nenhum .py de comportamento) | suíte existente verde sem tocar asserção |
| R3 | mapas do harness | jornada ponta a ponta |

## Plano de testes

### RED

- Comando: `uv run pytest apps/doctor apps/scheduler apps/intake -q` (testes novos de conteúdo/marcadores antes)
- Falha esperada: layout/marcadores novos ausentes.

### GREEN / verificação local

- `uv run pytest -q`, `mypy`, `ruff check/format` — exit 0
- jornada + capturas vs ats-web

## Critérios de aceitação

- [ ] R1–R3 verdes; nenhum teste de comportamento ajustado para passar
- [ ] Ordem clínica dos cards preservada

## Relatório de evidência

`/tmp/hmd-slice-i003-report.md` (mesmo formato dos slices 001-002).

## Handoff

Implementar apenas este slice. Escalar se qualquer template exigir
dado novo de view/presenter (fora do aprovado). Não iniciar o slice 004.
