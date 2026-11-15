# Slice 001 — Pendentes do médico: paridade da página completa

## 1. Identity

- **Slice ID:** OLI-001
- **Change ID:** `operational-lists-ui-parity`
- **Task:** `tasks.md` §1 (`1.1`–`1.4`)

## 2. Objective

Entregar a aba **Pendentes** do médico com container, título, filtro, alerta,
cards, estados de reserva, vazio e responsividade no padrão aprovado, sem mudar
seu escopo de acesso ou comportamento operacional.

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/operational-lists-ui-parity/proposal.md`
- `openspec/changes/operational-lists-ui-parity/design.md` — D1–D6, D8, D10, D12–D13
- `openspec/changes/operational-lists-ui-parity/specs/doctor-decision/spec.md`
- `templates/doctor/queue.html`
- `templates/doctor/_queue_content.html`
- `apps/doctor/views.py`
- `static/css/app.css`
- `static/js/doctor_queue_filter.js`
- `apps/doctor/tests/test_queue.py`
- `apps/doctor/tests/test_queue_parity.py`
- `.pi/skills/verify-hmd/SKILL.md`
- `.pi/skills/verify-hmd/features/fila-medica-busca-refresh.md`
- Molde somente-leitura: `/projects/dev/ats-web/templates/doctor/queue.html`,
  `/projects/dev/ats-web/templates/doctor/_queue_content.html` e
  `/projects/dev/ats-web/static/css/app.css`

## 4. Requirements

- **R1 — Página alinhada.** Um único `h1.page-title` «Casos aguardando decisão»
  via `page_title`; remover `col-lg-10`/`mt-4`; título, filtros, alerta e cards
  ocupam o container integral, alinhado ao header.
- **R2 — Um filtro de subtipo.** Apenas o GET server-side `?subtype=` filtra por
  subtipo; em pendentes suas opções carregam as contagens já disponíveis.
  Remover `data-doctor-exam-filter`, a fileira de badges e toda dependência do
  JS em `typeSelect`. Busca client-side e sua reaplicação pós-swap continuam.
- **R3 — Card operacional compacto.** Cada card pendente tem uma única
  superfície, `.case-card > .row`, conteúdo `col-lg-8` e tempo/ação `col-lg-4`.
  Exibe nome/idade, ocorrência, sexo/origem quando presentes, procedimentos e
  subtipos, `Dias em tela: N` e exatamente um `Aguardando há`.
- **R4 — Ações e locks.** Caso livre tem link «Avaliar», próprio tem «Continuar
  avaliação» e reservado por outro tem botão desabilitado + portador, sem link.
  CTA permitido é real e usa `stretched-link`; não existe `list-group-item` no
  card.
- **R5 — Alertas/vazio/responsivo.** Contagem usa warning; vazio usa apenas um
  info. Em 1280 px as regiões são 8/4 e a ação fica à direita; em 390 px elas
  empilham, com wrap, alvo ≥44 px e sem overflow.
- **R6 — Comportamento preservado.** `tab`, `subtype`, ordenação, guards,
  paginação, partial de 20 s, contadores do header, queries limitadas e destinos
  dos CTAs permanecem; a aba decididos ainda não recebe o card do slice 003.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | testes de título/container + medidas computadas de alinhamento em 1280 px |
| R2 | testes de um único seletor/opções/GET + jornada de filtro, busca e polling |
| R3 | testes de estrutura/conteúdo/contagem de espera + medidas 8/4 e PNG |
| R4 | testes dos três estados de lock, presença/ausência de link e destino |
| R5 | testes de vazio + medidas mobile, wrap, alvo e `scrollWidth` |
| R6 | suíte de fila/partial/guards/paginação/query count e regressão dos drives afetados |

## 5. Expected blast radius

```yaml
expected_files:
  - templates/doctor/queue.html
  - templates/doctor/_queue_content.html
  - apps/doctor/views.py
  - static/css/app.css
  - static/js/doctor_queue_filter.js
  - apps/doctor/tests/test_queue.py
  - apps/doctor/tests/test_queue_parity.py
  - apps/doctor/tests/test_page_titles.py
  - .pi/skills/verify-hmd/features/fila-medica.md
  - .pi/skills/verify-hmd/features/fila-medica-busca-refresh.md
  - .pi/skills/verify-hmd/features/home-por-papel.md
  - .pi/skills/verify-hmd/scripts/browser/drive.mjs
  - .pi/skills/verify-hmd/scripts/browser/drive-fila-medica-busca-refresh.mjs
  - .pi/skills/verify-hmd/scripts/browser/drive-upload-dragdrop.mjs
  - .pi/skills/verify-hmd/scripts/browser/drive-detalhe-papeis.mjs
allowed_incidental_files:
  - outro teste/driver existente somente para substituir seletor antigo comprovadamente quebrado, com justificativa no relatório
out_of_scope:
  - apps/doctor/urls.py, services, models, migrations, FSM e presenters clínicos
  - templates/doctor/_case_cards.html, decide.html e case_detail.html
  - card novo da aba decididos (OLI-003)
  - scheduler, NIR, painel, header global e partials _nav (OLI-005)
```

**Preservar:** matriz de acesso por subtipo, expiração lazy de leases, query
count sem N+1, partial idêntico ao assembly da página e todos os dados/atributos
necessários à busca.

## 6. Failing-before plan

Após escrever/adaptar os testes, mas antes do código de produção:

```bash
uv run pytest \
  apps/doctor/tests/test_queue.py \
  apps/doctor/tests/test_queue_parity.py \
  apps/doctor/tests/test_page_titles.py -q
```

**Falhas esperadas:** título antigo, `col-lg-10`, dois mecanismos de filtro,
card sem regiões 8/4, `list-group-item`, espera repetida, badge `N d em tela`,
ausência de demografia e vazio em card em vez de alerta info. Registrar comando,
exit status e falhas representativas no relatório; não obter RED por teste mal
escrito ou fixture inválida.

## 7. Implementation constraints

- Seguir a política de engenharia em `AGENTS.md`; menor mudança correta, sem
  abstração compartilhada antecipada.
- CSS novo somente sob o escopo da lista operacional; não alterar regras globais
  de `.case-card`/`.summary-box` nem tokens.
- Não adicionar sumário clínico, suporte ou fluxo CHD ao card; usar somente os
  dados aprovados em D4.
- Não projetar disposições/horários de decididos neste slice além do necessário
  para não quebrar o markup antigo temporário; isso é OLI-003.
- Atualizar drivers antigos apenas para os novos títulos/seletores; não modificar
  os fluxos de upload/detalhe que eles verificam.
- Manter testes antigos cujo comportamento continua válido; atualizações de
  expectativa devem citar o requisito novo correspondente.

## 8. Validation

**Focused validation**

- Comando RED da seção 6, seguido do mesmo conjunto verde após implementação.
- Testes de lock, ordenação, filtro, paginação, partial e query count do médico.
- Testes de navegação existentes que compartilham os templates renderizados.
- `git diff --check` e inspeção de que não há edição no projeto-fonte.

**Project-required gates**

- Executar o quality gate completo definido em `AGENTS.md` §2 antes de reportar;
  registrar todos os comandos e exit status.

**Runtime/verification evidence**

- Rodar a jornada `fila-medica-busca-refresh` pelo `verify-hmd`, com login real,
  em instância isolada própria e cleanup ao fim.
- Evidência em 1280 e 390 px: PNG, snapshot e JSON com medidas do container,
  card/regiões, CTA, alvo e overflow.
- Provar filtro `subtype` no server, busca sem reload e busca preservada após o
  poll; casos fora do subtipo/página não aparecem.
- Rodar os drives afetados pelos novos títulos/seletores. Comparar com a
  referência no mesmo viewport quando disponível, sem exigir pixel-perfect.

## 9. Acceptance criteria

- [ ] R1–R6 têm evidência automatizada e/ou browser correspondente.
- [ ] Existe exatamente um seletor de subtipo e uma espera por card pendente.
- [ ] Nenhum card modificado contém `list-group-item`; toda ação permitida é CTA real.
- [ ] As medidas desktop/mobile e os estados de lock/vazio estão nas evidências.
- [ ] Guards, ordenação, paginação, partial, destinos e query count não regrediram.
- [ ] Quality gate e jornadas focadas/afetadas terminam com exit 0.
- [ ] Não há alteração fora do blast radius sem justificativa aceita.
- [ ] Relatório e revisão independente estão concluídos antes da revisão do dono.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se surgir necessidade de alterar acesso,
FSM/persistência, regra clínica, semântica das abas, contadores ou paginação; se
o card exigir um dado clínico não aprovado; se for impossível evitar regressão
na aba decididos sem redesenhá-la além do temporário permitido; ou se for
necessário ampliar materialmente o escopo CSS/rotas/serviços.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-oli-001-report.md`

Contém status `READY_FOR_REVIEW` ou `BLOCKED_NEEDS_DECISION`, R1–R6 → evidência,
arquivos alterados, RED/passing-after, comandos + exit status, paths do run e
PNG/snapshot/JSON, medidas, comparação com a referência quando disponível,
expansões de blast radius, riscos e decisões pendentes.

## 12. Worker handoff

Implement only this approved slice. Reconstruct context from the listed files;
do not rely on prior conversation. Follow `AGENTS.md`, produce failing-before
evidence, implement the smallest correct change and run focused/project/runtime
validation. Do not update `tasks.md`, commit, push, merge, archive, or start
another slice. If a new decision is required, stop with
`BLOCKED_NEEDS_DECISION` and evidence. Otherwise finish with
`READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-oli-001-report.md`.
