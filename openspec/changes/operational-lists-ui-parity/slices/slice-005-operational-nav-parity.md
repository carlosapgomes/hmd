# Slice 005 — Navegação operacional e integração visual

## 1. Identity

- **Slice ID:** OLI-005
- **Change ID:** `operational-lists-ui-parity`
- **Task:** `tasks.md` §5 (`5.1`–`5.4`)

## 2. Objective

Harmonizar pills e contadores das áreas NIR/médico/agendador aos tokens HMD e
fechar a regressão visual/comportamental das quatro páginas entregues nos slices
anteriores.

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/operational-lists-ui-parity/proposal.md`
- `openspec/changes/operational-lists-ui-parity/design.md` — D11–D13 e riscos
- `openspec/specs/ui-shell/spec.md` — identidade e navegação vigentes, preservadas
- Deltas `doctor-decision`, `scheduling` e `intake-nir` deste change
- `templates/intake/_nav.html`
- `templates/doctor/_nav.html`
- `templates/scheduler/_nav.html`
- `templates/base.html` — leitura; não editar
- `static/css/app.css`
- `apps/accounts/tests/test_header_nav.py`
- `apps/accounts/tests/test_queue_counters.py`
- `.pi/skills/verify-hmd/SKILL.md`
- `.pi/skills/verify-hmd/features/header-nav-4-papeis.md`
- Relatórios aceitos de OLI-001–004
- Molde somente-leitura: `_nav.html` dos três papéis e
  `/projects/dev/ats-web/static/css/app.css` (`.app-nav`, `.nav-count-badge`)

## 4. Requirements

- **R1 — Pills no padrão HMD.** Faixa separada no header; links com pill,
  hover/focus visível e aba ativa com fundo branco + texto `--hospital-primary`.
  Não usar o azul default do Bootstrap nem alterar tokens/identidade.
- **R2 — Um mecanismo de contador.** Contadores somente por `data-count` +
  pseudo-elemento; remover badges `<span>` inline dos links. Pendentes do
  médico/agendador usa estilo de ação; demais contadores são neutros; zero fica
  oculto e nenhum número aparece duplicado.
- **R3 — Semântica preservada.** Rotas, labels, `?tab=`, `?subtype=`, `filter_qs`
  do NIR, `aria-current`, papel ativo, contadores e páginas transversais sem aba
  ativa permanecem. `Decididos`/`Processados` não ganham «Hoje».
- **R4 — Desktop/mobile.** Em 1280 px pills e contadores ficam alinhados; em
  390 px fazem wrap sem overflow, links mantêm alvo ≥44 px e foco acessível.
  Sino, avatar, sessão, toggler e painel não mudam.
- **R5 — Integração.** As quatro páginas conservam os requisitos OLI-001–004:
  container, filtros, cards, espera/resultado, CTAs, locks, vazios, polling e
  medidas desktop/mobile; superfícies vizinhas usadas pelos drives não regredem.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | guardas CSS + computed styles da aba ativa/hover/focus + PNG |
| R2 | testes de markup sem badge inline + computed `::after`, zero e contagem única |
| R3 | suíte header/queue counters e testes de URLs/filtros/papel |
| R4 | medidas e capturas 1280/390 + alvo, wrap e overflow |
| R5 | matriz das jornadas OLI-001–004 + quality gate e drives afetados |

## 5. Expected blast radius

```yaml
expected_files:
  - templates/intake/_nav.html
  - templates/doctor/_nav.html
  - templates/scheduler/_nav.html
  - static/css/app.css
  - apps/accounts/tests/test_header_nav.py
  - apps/accounts/tests/test_queue_counters.py
  - apps/accounts/tests/test_operational_nav_css.py
  - .pi/skills/verify-hmd/features/header-nav-4-papeis.md
  - .pi/skills/verify-hmd/scripts/browser/drive-header-nav.mjs
allowed_incidental_files:
  - expectativa de teste de página diretamente afetada pela retirada dos badges inline, com justificativa
  - feature/driver de OLI-001–004 somente para corrigir regressão comprovada da navegação
out_of_scope:
  - templates/base.html e header/sessão/sino/manual/avatar/toggler
  - views, context processors, rotas, models, migrations e regras de contagem
  - dashboard e navegação admin/manager
  - redesign dos cards entregues em OLI-001–004
  - PROJECT_CONTEXT.md e archive (somente no fechamento posterior do change)
```

**Preservar:** três tokens reservados, labels e destinos, contagens calculadas
pelos guards, `filter_qs` do NIR e páginas sem aba ativa fora das filas.

## 6. Failing-before plan

Depois de escrever/adaptar os testes e antes de alterar produção:

```bash
uv run pytest \
  apps/accounts/tests/test_header_nav.py \
  apps/accounts/tests/test_queue_counters.py \
  apps/accounts/tests/test_operational_nav_css.py -q
```

**Falhas esperadas:** regras `.app-nav`/`.nav-count-badge` ausentes ou sem tokens,
badges inline ainda presentes, contador sem mecanismo único e zero visível. O RED
deve cobrir os três papéis e um caso sem contadores. Registrar falhas reais e
exit status; não usar simples ausência de arquivo como única prova se o teste já
puder renderizar a página.

## 7. Implementation constraints

- Seguir `AGENTS.md`; menor mudança correta, sem reabrir o design dos cards.
- Adaptar o molde da referência aos tokens HMD; não copiar cores nem alterar o
  azul primário reservado.
- Não manter markup inline “para compatibilidade” se ele produzir dupla contagem;
  atualizar os testes que dependiam desse markup para o contrato D11.
- Não alterar `base.html`, sino ou navegação global do painel.
- Manter `data-count` inclusive quando zero e supressão por CSS, ou ausência
  equivalente do pseudo-elemento; nunca renderizar zero como badge visível.
- Não atualizar `PROJECT_CONTEXT.md` nem arquivar neste slice de implementação.

## 8. Validation

**Focused validation**

- RED da seção 6 e mesmo conjunto verde após implementação.
- Testes dos três `_nav`, contagens/zero, aba ativa por rota, páginas sem aba
  ativa, links com subtipo/filtros e identidade CSS.
- Regressão das suítes das quatro áreas afetadas pela retirada dos badges inline.

**Project-required gates**

- Quality gate completo de `AGENTS.md` §2, com todos os resultados registrados.

**Runtime/verification evidence**

- Executar `header-nav-4-papeis` em instância `verify-hmd` isolada, com cleanup;
  computed styles de active/hover/focus e `::after`, incluindo zero e mobile.
- Executar a regressão das jornadas `fila-medica`,
  `fila-medica-busca-refresh`, `fila-medica-decididos`,
  `fila-agendamento-busca-refresh`, `meus-casos-filtros` e o drive genérico;
  repetir drives de detalhe/upload cujos seletores foram tocados nos slices.
- PNG/snapshot/JSON em 1280 e 390 px, sem contadores duplicados ou overflow;
  alvo dos links ≥44 px.
- Consolidar uma matriz dos cinco slices com requisito, evidência, run e status;
  comparar com a referência quando disponível.

## 9. Acceptance criteria

- [ ] R1–R5 possuem evidência correspondente.
- [ ] Aba ativa/hover/focus usam tokens HMD e são visíveis por computed style.
- [ ] Cada contador tem uma única representação; zero está oculto; nenhum badge inline permanece.
- [ ] Labels, rotas, contagens, papel ativo, `aria-current` e filtros não mudaram.
- [ ] Desktop/mobile não têm overflow; links têm alvo ≥44 px.
- [ ] A matriz das quatro páginas não apresenta regressão em cards/filtros/locks/polling.
- [ ] Quality gate e todas as jornadas exigidas terminam com exit 0 e cleanup.
- [ ] Relatório final e revisão independente estão concluídos; change não é arquivado pelo worker.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se for necessário alterar contagem, rota,
label, papel, base/header global ou tokens; se a retirada dos badges inline
exigir mudança de comportamento; se uma regressão em OLI-001–004 exigir novo
design; ou se for necessário ampliar o escopo para painel/notificações/outras
páginas.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-oli-005-report.md`

Contém status, R1–R5 → evidência, arquivos, RED/passing-after, comandos/exit
status, runs/artefatos, computed styles, medidas, matriz consolidada dos cinco
slices, regressões, blast radius, riscos e pendências para o archive posterior.

## 12. Worker handoff

Implement only this approved slice. Reconstruct context from the listed files;
do not rely on prior conversation. Follow `AGENTS.md`, produce failing-before
evidence, implement the smallest correct change and run focused/project/runtime
validation. Do not update `tasks.md`, commit, push, merge, archive, or start
another slice/change. If a new decision is required, stop with
`BLOCKED_NEEDS_DECISION` and evidence. Otherwise finish with
`READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-oli-005-report.md`.
