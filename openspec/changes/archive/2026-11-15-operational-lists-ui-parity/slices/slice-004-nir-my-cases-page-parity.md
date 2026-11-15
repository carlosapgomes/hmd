# Slice 004 — Meus casos do NIR: paridade da página e da lista compacta

## 1. Identity

- **Slice ID:** OLI-004
- **Change ID:** `operational-lists-ui-parity`
- **Task:** `tasks.md` §4 (`4.1`–`4.4`)

## 2. Objective

Entregar **Meus casos** com página, navegação, filtros, cabeçalho da lista,
cards compactos, exceções, estados vazios e mobile no padrão aprovado, sem mudar
o escopo por criador ou os fluxos de detalhe/encerramento.

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/operational-lists-ui-parity/proposal.md`
- `openspec/changes/operational-lists-ui-parity/design.md` — D1–D5, D9–D10, D12–D13
- `openspec/changes/operational-lists-ui-parity/specs/intake-nir/spec.md`
- `templates/intake/my_cases.html`
- `templates/intake/_my_cases_content.html`
- `templates/intake/_nav.html`
- `apps/intake/views.py` — `_build_my_cases_context`
- `static/css/app.css`
- `static/js/nir_queue_filter.js`
- `apps/intake/tests/test_my_cases.py`
- `apps/intake/tests/test_my_cases_filters.py`
- `.pi/skills/verify-hmd/SKILL.md`
- `.pi/skills/verify-hmd/features/meus-casos-filtros.md`
- Molde somente-leitura: `/projects/dev/ats-web/templates/intake/my_cases.html`,
  `/projects/dev/ats-web/templates/intake/_my_cases_content.html` e
  `/projects/dev/ats-web/static/css/app.css`

## 4. Requirements

- **R1 — Página e navegação única.** Título «Todos os seus encaminhamentos» via
  `page_title`, container integral e nenhum CTA «Enviar relatório» no topo.
  Remover as pills internas; Meus Casos/Casos Encerrados do header são a única
  navegação entre as visões.
- **R2 — Filtros preservados.** Form server-side `q/status/procedure_type`
  permanece na grade; links do header para `active/closed` carregam `filter_qs`
  quando aplicável. Escopo por criador, AND, mínimo de 3 caracteres, sufixo de
  desambiguação, partial e contadores permanecem.
- **R3 — Cabeçalho neutro da lista.** Lista envolvida por card `p-4`, com
  «Meus Casos», badge do total filtrado e nota de atualização; nenhum
  `alert-warning` de contagem e nenhuma segunda contagem visual concorrente.
- **R4 — Card compacto 4/3/3/2.** Uma superfície com identidade/procedimentos,
  recebimento/complementos, status/alertas e CTA «Ver detalhes» em quatro regiões
  desktop. Nome usa `.patient-name`; demografia disponível e sufixo aparecem nos
  metadados. Não há `summary-box`, `waiting-time` ou `list-group-item`.
- **R5 — Exceções e vazio preservados.** Status semântico, falha, retenção,
  revisão e encerramento administrativo com motivo continuam compactos. Vazio é
  centralizado: com filtros oferece «Limpar filtros»; sem filtros orienta a aba
  e oferece «Novo Encaminhamento» quando aplicável.
- **R6 — Mobile e comportamento.** Em 1280 px as quatro regiões aparecem; em
  390 px empilham, badges fazem wrap, CTA fica full-width com alvo ≥44 px e sem
  overflow. Escopo, filtros, abas, partial de 20 s, detalhe e URLs não regredem.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | testes de título/ausência de duplicação + PNG/medidas |
| R2 | testes de href com `filter_qs`, AND, escopo e partial + jornada de ida/volta |
| R3 | testes do cabeçalho/badge/ausência de warning + jornada pós-refresh |
| R4 | testes de estrutura/conteúdo/ausência de resumo/espera + medidas desktop |
| R5 | fixtures de falha/retenção/admin-closure e três variantes de vazio |
| R6 | medidas mobile/CTA/overflow + suíte de filtros/partial/detalhe |

## 5. Expected blast radius

```yaml
expected_files:
  - templates/intake/my_cases.html
  - templates/intake/_my_cases_content.html
  - templates/intake/_nav.html
  - apps/intake/views.py
  - static/css/app.css
  - static/js/nir_queue_filter.js
  - apps/intake/tests/test_my_cases.py
  - apps/intake/tests/test_my_cases_filters.py
  - apps/intake/tests/test_page_titles.py
  - .pi/skills/verify-hmd/features/meus-casos-filtros.md
  - .pi/skills/verify-hmd/scripts/browser/drive-meus-casos-filtros.mjs
allowed_incidental_files:
  - teste de header existente somente para o novo contrato de filtros nos links do NIR
  - outro driver existente somente para sincronizar título/CTA, com justificativa
out_of_scope:
  - apps/intake/urls.py, services.py, forms.py, models e migrations
  - templates/intake/case_detail.html, home.html e corrected_resubmission.html
  - doctor/scheduler (OLI-001–003), painel e demais partials _nav (OLI-005)
  - mudança de paginação, escopo por criador ou semântica CLEANED
```

**Preservar:** escopo por criador, `filter_qs` no partial, contadores por aba,
sufixo de desambiguação, badges de retenção e dados de encerramento
administrativo. O live region do contador pode ficar `visually-hidden` para
acessibilidade, sem gerar segunda contagem visual.

## 6. Failing-before plan

Depois de escrever/adaptar os testes e antes de alterar produção:

```bash
uv run pytest \
  apps/intake/tests/test_my_cases.py \
  apps/intake/tests/test_my_cases_filters.py \
  apps/intake/tests/test_page_titles.py -q
```

**Falhas esperadas:** título antigo, CTA/pills duplicados, links do header sem
filtros, alerta de contagem, `summary-box`/`waiting-time`, ausência das regiões
4/3/3/2 e CTA «Ver detalhes». Incluir cenários com filtros na troca de aba,
falha/retenção/admin-closure e vazio com/sem filtros. Registrar exit status e
falhas representativas; RED de fixture não é aceito.

## 7. Implementation constraints

- Seguir `AGENTS.md`; menor mudança correta e sem antecipar OLI-005.
- Não alterar queryset, escopo, regras dos filtros, ordenação ou cálculo dos
  sufixos/contadores; alterações em `views.py` limitam-se à projeção visual.
- Links do `_nav` só anexam `filter_qs` no contexto de meus casos; não contaminar
  Novo Encaminhamento, detalhe ou outras páginas com querystring vazia/legada.
- Manter uma única contagem visual; se o live region JS for mantido, ele deve ser
  visualmente oculto e continuar atualizado após o swap.
- CSS somente no escopo da lista; não alterar cards do painel/detalhe nem reduzir
  o mínimo de 44 px.
- Não remover informações de falha, revisão, retenção ou encerramento para obter
  densidade; essas exceções podem ocupar mais altura quando necessário.

## 8. Validation

**Focused validation**

- RED da seção 6 e mesmo conjunto verde após implementação.
- Testes de escopo por criador, filtros AND/curtos/inválidos existentes, hrefs
  das abas com filtros, partial, sufixo, retenção e admin-closure.
- Regressão do detalhe do NIR e testes de navegação afetados.

**Project-required gates**

- Quality gate completo de `AGENTS.md` §2, com resultados registrados.

**Runtime/verification evidence**

- Jornada `meus-casos-filtros` em instância `verify-hmd` isolada, com cleanup;
  PNG/snapshot/JSON em 1280 e 390 px.
- Provar ida e volta pelas abas do header com filtros preservados, filtros AND,
  escopo de criador e refresh com filtros; casos fora do escopo não aparecem.
- Medidas do container e das regiões 4/3/3/2, CTA full-width/alvo no mobile,
  wrap e `scrollWidth <= clientWidth`.
- Evidência de cabeçalho neutro, uma contagem visual, falha/retenção e vazio.
- Comparação com a referência no mesmo viewport quando disponível.

## 9. Acceptance criteria

- [ ] R1–R6 possuem evidência correspondente.
- [ ] Não há pills/CTA duplicados; trocar de aba pelo header preserva os filtros.
- [ ] Cards têm quatro regiões no desktop, uma superfície e nenhuma espera/resumo redundante.
- [ ] Status, retenção, sufixo e encerramento administrativo continuam visíveis.
- [ ] Vazio orienta a ação correta; mobile tem CTA full-width/alvo ≥44 px e sem overflow.
- [ ] Escopo, filtros, partial, detalhe e contadores não regrediram.
- [ ] Quality gate e jornada focada/afetadas terminam com exit 0.
- [ ] Relatório e revisão independente estão concluídos antes da revisão do dono.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se for necessário mudar escopo por criador,
semântica de abas/filtros/CLEANED, detalhe ou encerramento; se a navegação única
não puder preservar filtros sem alterar outras páginas; se exceções não couberem
sem remover dados; ou se surgir necessidade de rota, migration ou escopo global.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-oli-004-report.md`

Contém status, R1–R6 → evidência, arquivos, RED/passing-after, comandos/exit
status, run/artefatos, medidas desktop/mobile, filtros/troca de abas/polling,
exceções/vazios, blast radius, riscos e decisões pendentes.

## 12. Worker handoff

Implement only this approved slice. Reconstruct context from the listed files;
do not rely on prior conversation. Follow `AGENTS.md`, produce failing-before
evidence, implement the smallest correct change and run focused/project/runtime
validation. Do not update `tasks.md`, commit, push, merge, archive, or start
another slice. If a new decision is required, stop with
`BLOCKED_NEEDS_DECISION` and evidence. Otherwise finish with
`READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-oli-004-report.md`.
