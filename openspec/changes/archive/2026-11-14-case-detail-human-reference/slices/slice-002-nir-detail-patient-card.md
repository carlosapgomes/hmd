# Slice 002 — Detalhe do NIR: card de identificação + referência humana

```yaml
expected_files:
  - templates/intake/case_detail.html
  - apps/intake/tests/test_case_detail.py
allowed_incidental_files: []
out_of_scope:
  - flash messages / home / corrected_resubmission / my_cases (slice 003/004)
  - views de intake (o objeto `case` já carrega tudo que o template precisa)
  - qualquer outro template
```

## Objetivo

No detalhe do caso do NIR (`templates/intake/case_detail.html`): o hero com
UUID vira card «Identificação do paciente» (nome + idade, badge de status,
timestamps), e `<title>`, breadcrumb e link de «Reenvios corrigidos» usam a
referência humana — uid completo deixa de aparecer em texto visível na
página.

## Contexto necessário

- `templates/intake/case_detail.html` — linhas-alvo: `<title>` `:4`,
  breadcrumb `:12`, hero `:17-36` (h1 + badges condicionais de status +
  timestamps + nº), link de correção `:104-107`.
- Padrão visual a espelhar: `templates/scheduler/case_detail.html:18-40`
  (card header com h1 «Identificação do paciente» + badge; body com `dl`).
  Decisões do dono: nome + idade apenas (não a demografia completa);
  fallback de nº = `uid <8 chars>` (D2 do design).
- O contexto já passa o objeto `case` (`apps/intake/views.py:418-429`) e
  `corrections` é lista de `Case` (`:404`) → use as properties do slice 001:
  `case.display_reference`, `correction.disambiguated_reference`,
  `case.patient_name`, `case.patient_age`, `case.agency_record_number`.
- Idade: padrão `my_cases.html:64` — `{{ patient_age }} a` exibida só quando
  não-nula («0 a» exibe); nome ausente → `—`.
- Testes existentes: `apps/intake/tests/test_case_detail.py` — revisar pins
  que assertem `Caso <uid>`/`case_id` em texto visível (uid no href
  continua válido).

## Requisitos verificáveis

- **R1** — `<title>` e breadcrumb exibem «Caso <nº>» (ou `Caso uid <8>`
  quando sem nº); o uid completo não aparece em texto visível (só em hrefs).
- **R2** — Hero substituído por card «Identificação do paciente»: header com
  h1 + badge de status (classes condicionais atuais preservadas: FAILED
  danger, revisão warning/info, demais secondary), body com Paciente
  (nome/`—`), Idade (`N a`, omitida quando nula) e Nº de ocorrência
  (`display_reference`), além da linha de timestamps (criado/atualizado).
- **R3** — O card usa `display_reference` para o nº (fallback `uid <8>`
  quando vazio — hoje a linha `:35` usa `default:"—"`, passa a usar o helper).
- **R4** — Link de «Reenvios corrigidos» exibe
  `correction.disambiguated_reference` (nº · sufixo), mantendo o href por
  `correction.case_id`.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `templates/intake/case_detail.html` | testes novos em `test_case_detail.py` (title/breadcrumb com nº; uid completo ausente do texto visível) |
| R2 | `templates/intake/case_detail.html` | teste novo: card contém «Identificação do paciente», nome, `84 a` e badge de status |
| R3 | `templates/intake/case_detail.html` | teste novo: caso sem nº → `uid <8 chars>` presente, uid completo ausente |
| R4 | `templates/intake/case_detail.html` | teste novo: link de correção exibe `N · <8 chars>` com href correto |

## Plano de testes

### RED

- Comando: `uv run pytest apps/intake/tests/test_case_detail.py -q`
- Falha esperada: os testes novos falham — página atual renderiza
  `Caso <uuid>` no title/breadcrumb/hero e não contém o card de identificação
  (assert de `Identificação do paciente` / `Caso <nº>` / `N · <sufixo>`
  não encontra o texto).

### GREEN / verificação local

- `uv run pytest apps/intake/tests/test_case_detail.py -q` — exit 0
- `uv run pytest apps/intake/tests/ -q` — sem regressão no app (pins antigos
  atualizados onde assertavam o uid em texto visível)
- `uv run ruff check apps/intake && uv run ruff format --check apps/intake` — exit 0

## Critérios de aceitação

- [ ] R1-R4 verdes com os quatro testes novos
- [ ] Badges condicionais de status preservados (FAILED/revisão) no header do card
- [ ] Diff toca somente o template e o módulo de teste
- [ ] Pins antigos de uid-visível atualizados; pins de uid-em-href intactos

## Handoff

Se o card exigir dado que não está no objeto `case`, ou se algum teste exigir
mudar a view, parar e escalar ao parent (blast radius prevê só template+tests).
