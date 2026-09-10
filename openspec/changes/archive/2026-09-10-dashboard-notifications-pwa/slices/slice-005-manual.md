# Slice 005: Manual de usuário por papel

## Objetivo

Página de manual em HTML descrevendo o ciclo do caso e as operações por
papel (NIR/médico/agendador) + transversais (notificações, painel, PWA),
link na navbar em nova aba.

## Contexto necessário

- Design D5 (`openspec/changes/dashboard-notifications-pwa/design.md`).
- ats-web SOMENTE-LEITURA: `templates/accounts/manual.html` +
  `apps/accounts/urls.py` (`manual/`, name `user_manual`) — adapte
  estrutura/seções ao HMD.
- Conteúdo VERDADEIRO do HMD (o manual deve refletir o fluxo real — fonte:
  specs promovidas + telas): ciclo NEW→…→CLEANED com pontos de cada papel;
  NIR: envio de relatório (PDFs) + **anexos jpg/png/pdf** (change 10),
  abas meus-casos ativos/encerrados, ciência do recebimento, reenvio
  corrigido; médico: fila por especialidade, detalhe com alertas de
  policy/prior-case/**cards de anexos com verificação de paciente**
  (mismatch = alerta consultivo), decidir por procedimento; agendador:
  fila abas, confirmar/denegar por unidade (unidade 1 texto parametrizado,
  unidade 2 fixo), **PDF do relatório**, intercorrência/reabertura
  (unidade 1); transversais: notificações/sino, painel gerencial,
  instalar o app (PWA).
- Navbar: `templates/base.html` área de ações — link manual
  `target="_blank"` (padrão ats-web).
- Rota login-required? ats-web exige login — siga o padrão (conteúdo
  descreve telas autenticadas).

## Requisitos verificáveis

- **R1** Rota `manual` (nome GLOBAL, sem namespace — P1 review; `/manual/`),
  login required, renderiza
  `templates/accounts/manual.html`.
- **R2** Template: visão geral do ciclo (texto/plinha de estados do HMD —
  sem inventar estados; confira `CaseStatus`), seções por papel com as
  operações REAIS (lista acima) e seção transversal (notificações, painel,
  PWA); HTML semântico imprimível (CSS print básico); sem dados dinâmicos
  de caso (estático).
- **R3** Navbar: link "Manual" visível a autenticados, nova aba.
- **R4** Testes: GET autenticado 200 com marcadores das seções por papel +
  menção a anexos/verificação de paciente + ciência/reenvio (assert
  anti-desatualização mínima); anônimo → redirect login; navbar contém o
  link em página autenticada.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/{urls,views}.py` | `test_manual_route_login_required` |
| R2 | `templates/accounts/manual.html` | `test_manual_sections_and_content` |
| R3 | `templates/base.html` | `test_navbar_manual_link` |
| R4 | `apps/accounts/tests/test_manual.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/{urls,views}.py   # rota user_manual
  - templates/accounts/manual.html
  - templates/base.html             # link Manual
  - apps/accounts/tests/test_manual.py

out_of_scope:
  - PDF do manual (follow-up); dashboard/notificações/PWA (outras slices)
  - imagens/screenshots no manual (texto estruturado apenas)
  - apps/cases (nenhuma mudança)
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_manual.py`
- Falha esperada: 404 na rota global `manual` (inexistente).

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/` — exit 0
- `uv run ruff check apps/accounts && uv run ruff format --check apps/accounts`
- `uv run mypy .`
- `openspec validate dashboard-notifications-pwa --strict` (delta
  case-management MODIFIED carregado desde o planejamento)

## Critérios de aceitação

- [ ] R1–R4 comprovados; manual descreve o fluxo REAL (anexos,
      verificação de paciente, unidades, intercorrência, ciência, reenvio)
- [ ] Sem estados/fluxos inventados (checar contra `CaseStatus`/specs)
- [ ] validate --strict PASS com todos os deltas do change
- [ ] Gate parcial do slice verde
