# Slice 002 — Pendentes do agendador: paridade da página completa

## 1. Identity

- **Slice ID:** OLI-002
- **Change ID:** `operational-lists-ui-parity`
- **Task:** `tasks.md` §2 (`2.1`–`2.4`)

## 2. Objective

Entregar a aba **Pendentes** do agendador com página e cards no modelo
operacional aprovado, filtro server-side por procedimento e decisões médicas
visíveis, preservando unidades, locks, paginação e processados.

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/operational-lists-ui-parity/proposal.md`
- `openspec/changes/operational-lists-ui-parity/design.md` — D1–D5, D7–D8, D10, D12–D13
- `openspec/changes/operational-lists-ui-parity/specs/scheduling/spec.md`
- `templates/scheduler/queue.html`
- `templates/scheduler/_queue_content.html`
- `apps/scheduler/views.py`
- `apps/scheduler/presenters.py` — somente para o mapeamento de disposição, sem reutilizar o detalhe completo
- `apps/cases/procedure_catalog.py`
- `static/css/app.css`
- `static/js/scheduler_queue_filter.js`
- `apps/scheduler/tests/test_views.py`
- `apps/scheduler/tests/test_queue_parity.py`
- `.pi/skills/verify-hmd/SKILL.md`
- `.pi/skills/verify-hmd/features/fila-agendamento-busca-refresh.md`
- Resultado aceito de OLI-001 e seu CSS, sem redesenhar o médico
- Molde somente-leitura: `/projects/dev/ats-web/templates/scheduler/queue.html`,
  `/projects/dev/ats-web/templates/scheduler/_queue_content.html` e
  `/projects/dev/ats-web/static/css/app.css`

## 4. Requirements

- **R1 — Página e alerta.** Título «Solicitações de agendamento aguardando
  confirmação», container integral e contagem/vazio em `alert-info`, sem warning
  ou segundo card vazio.
- **R2 — Filtro por procedimento.** `procedure_type` opcional, do catálogo,
  filtra rows declaradas antes da paginação; inválido equivale a vazio. Parâmetro
  preservado no form, `filter_qs`, paginação e `hx-get` do partial. Não há filtro
  de unidade nem filtro client-side limitado à página.
- **R3 — Card 8/4.** Uma superfície, conteúdo `col-lg-8`, tempo/ação `col-lg-4`,
  identidade/demografia, procedimentos/subtipos, disposição por procedimento,
  `Dias em tela: N` e exatamente um `Aguardando há`; sem `list-group-item` ou
  data de recebimento repetida.
- **R4 — Ações e locks.** Livre → link primário «Agendar»; próprio → «Continuar»;
  outro → desabilitado + portador, sem link. CTAs permitidos são reais e podem
  usar `stretched-link`; destinos inalterados.
- **R5 — Responsividade e processados.** 8/4 + ação à direita em 1280 px;
  empilhamento/wrap/alvo ≥44 px/sem overflow em 390 px. A aba `Processados`
  preserva conteúdo, `Recebido há`, unidade e apresentação fora deste redesenho.
- **R6 — Comportamento preservado.** Dois estados pendentes, ordenação, guards,
  paginação, polling de 20 s, busca client-side, contadores, unidade exibida e
  ausência de N+1 permanecem.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | testes de título/alerta/vazio + PNG e medidas do container |
| R2 | testes de queryset/filtro inválido/paginação/partial + jornada server-side |
| R3 | testes de estrutura, demografia, decisões e espera única + medidas 8/4 |
| R4 | testes dos três locks e links + jornada correspondente |
| R5 | medidas mobile/overflow + testes e captura da aba processada |
| R6 | suíte scheduler, query count e polling com filtro + busca ativos |

## 5. Expected blast radius

```yaml
expected_files:
  - templates/scheduler/queue.html
  - templates/scheduler/_queue_content.html
  - apps/scheduler/views.py
  - static/css/app.css
  - static/js/scheduler_queue_filter.js
  - apps/scheduler/tests/test_views.py
  - apps/scheduler/tests/test_queue_parity.py
  - apps/scheduler/tests/test_page_titles.py
  - .pi/skills/verify-hmd/features/fila-agendamento-busca-refresh.md
  - .pi/skills/verify-hmd/features/home-por-papel.md
  - .pi/skills/verify-hmd/scripts/browser/drive.mjs
  - .pi/skills/verify-hmd/scripts/browser/drive-fila-agendamento-busca-refresh.mjs
  - .pi/skills/verify-hmd/scripts/browser/drive-detalhe-papeis.mjs
allowed_incidental_files:
  - outro teste/driver existente somente para sincronizar título/seletor alterado, com justificativa
out_of_scope:
  - apps/scheduler/urls.py, services.py, forms.py, presenters.py e models/migrations
  - templates/scheduler/case_detail.html e regras de confirmação/negação/reabertura
  - apps/doctor (OLI-001/003), NIR (OLI-004), painel e partials _nav (OLI-005)
  - novo filtro na aba processados
```

**Preservar:** expiração lazy, `prefetch_related`, busca e seus `data-*`, fila
sem diferenciação por unidade e comportamento da aba processada.

## 6. Failing-before plan

Depois de escrever/adaptar os testes e antes de alterar produção:

```bash
uv run pytest \
  apps/scheduler/tests/test_views.py \
  apps/scheduler/tests/test_queue_parity.py \
  apps/scheduler/tests/test_page_titles.py -q
```

**Falhas esperadas:** `procedure_type` ausente/sem efeito, título antigo,
warning em vez de info, card sem 8/4, espera repetida, disposição ausente e CTA
«Abrir caso» em vez de «Agendar». Incluir caso com dois tipos/disposições,
filtro inválido e caso fora da página. Registrar falhas representativas e exit
status; fixture inválida não conta como RED.

## 7. Implementation constraints

- Seguir `AGENTS.md`; menor mudança correta e sem antecipar OLI-003–005.
- Aplicar o filtro no queryset server-side antes de `Paginator`; não mascarar
  resultado parcial com JS e não alterar os contadores globais das abas.
- Projetar `doctor_disposition` e rótulo das rows já carregadas; não importar o
  presenter completo do detalhe nem consultar events por card.
- Não criar campos `Suporte`, `Fluxo` ou diagnóstico CHD.
- CSS apenas no escopo da lista; não modificar globalmente `.case-card` ou o
  painel e manter o markup de processados fora do novo ramo pendente.
- Alterações em outros drivers limitam-se a sincronizar título/seletor.

## 8. Validation

**Focused validation**

- RED da seção 6 e mesmo conjunto verde após implementação.
- Testes de filtro por tipo, inválido, AND com aba, propagação em paginação e
  partial, decisões mistas, locks, ordenação e query count.
- Regressão da aba processada e do detalhe de agendamento.

**Project-required gates**

- Quality gate completo de `AGENTS.md` §2, com todos os resultados registrados.

**Runtime/verification evidence**

- Jornada `fila-agendamento-busca-refresh` em instância `verify-hmd` isolada,
  com cleanup; PNG/snapshot/JSON em 1280 e 390 px.
- Provar filtro no server antes da paginação, valor inválido ignorado, busca sem
  reload, filtro + busca preservados no poll e caso fora do filtro/página ausente.
- Medidas do container, 8/4, CTA, alvo e overflow; locks e decisões por
  procedimento visíveis; captura da aba processada sem regressão.
- Comparação com a referência no mesmo viewport quando disponível.

## 9. Acceptance criteria

- [ ] R1–R6 possuem evidência correspondente.
- [ ] `procedure_type` filtra no server e viaja por form, paginação e partial.
- [ ] Cada card pendente tem uma espera, decisões por procedimento e CTA correto.
- [ ] Alerta/vazio são info; medidas desktop/mobile e alvo ≥44 px estão comprovados.
- [ ] Processados, unidades, locks, guards, paginação, polling e queries não regrediram.
- [ ] Quality gate e jornadas focadas/afetadas terminam com exit 0.
- [ ] Relatório e revisão independente estão concluídos antes da revisão do dono.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se o filtro exigir mudança de acesso,
contadores, unidade ou regra de agendamento; se decisões não puderem vir das rows
sem query por card; se processados precisar de redesign para manter o partial
coerente; ou se surgir necessidade de migration, serviço, rota ou dado clínico
não aprovado.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-oli-002-report.md`

Contém status, R1–R6 → evidência, arquivos, RED/passing-after, comandos/exit
status, run e artefatos browser, medidas, evidência de filtro/polling/locks e
processados, blast radius, riscos e decisões pendentes.

## 12. Worker handoff

Implement only this approved slice. Reconstruct context from the listed files;
do not rely on prior conversation. Follow `AGENTS.md`, produce failing-before
evidence, implement the smallest correct change and run focused/project/runtime
validation. Do not update `tasks.md`, commit, push, merge, archive, or start
another slice. If a new decision is required, stop with
`BLOCKED_NEEDS_DECISION` and evidence. Otherwise finish with
`READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-oli-002-report.md`.
