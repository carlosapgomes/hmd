# Slice 003 — Decididos do médico: paridade da página e do resultado

## 1. Identity

- **Slice ID:** OLI-003
- **Change ID:** `operational-lists-ui-parity`
- **Task:** `tasks.md` §3 (`3.1`–`3.4`)

## 2. Objective

Entregar a aba **Decididos** do médico com página alinhada e cards orientados às
decisões por procedimento, eliminando a aparência de espera e preservando o
acesso read-only.

## 3. Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `openspec/changes/operational-lists-ui-parity/proposal.md`
- `openspec/changes/operational-lists-ui-parity/design.md` — D1–D6, D8, D10, D12–D13
- `openspec/changes/operational-lists-ui-parity/specs/doctor-decision/spec.md`
- `templates/doctor/queue.html`
- `templates/doctor/_queue_content.html`
- `apps/doctor/views.py`
- `apps/doctor/presenters.py` — apenas referência de labels/formatação; não chamar o detalhe por card
- `apps/cases/models.py` — `CaseProcedure`/`DoctorDisposition`
- `apps/doctor/tests/test_queue.py`
- `apps/doctor/tests/test_queue_parity.py`
- `.pi/skills/verify-hmd/SKILL.md`
- Resultado aceito de OLI-001; pendentes não pode regredir
- Molde somente-leitura: `/projects/dev/ats-web/templates/doctor/queue.html` e
  `/projects/dev/ats-web/templates/doctor/_queue_content.html`

## 4. Requirements

- **R1 — Página decidida.** Um `h1.page-title` «Casos decididos», container
  integral, mesmo filtro único de subtipo sem contagens de pendentes e sem linha
  «Pendentes por subtipo». Não introduzir «Decididos Hoje» nem recorte de data.
- **R2 — Resultado por procedimento.** Cada card exibe identidade/demografia e,
  para cada procedimento declarado, label/subtipo, disposição e data/hora de
  `doctor_decided_at`. Aprovado e negado têm badges semânticos distintos.
- **R3 — Decisões mistas.** Um caso aprovado + negado mostra ambas as rows e
  badges; não há agregação case-level nem `status-done` verde representando toda
  decisão.
- **R4 — Estrutura e ação.** Uma superfície, conteúdo `col-lg-8`, resultado/ação
  `col-lg-4` e CTA outline real «Ver detalhes» para `doctor:case_detail`, com
  `stretched-link`; nenhum `list-group-item`.
- **R5 — Sem espera e com vazio/mobile corretos.** Nenhum `Aguardando há`,
  `Recebido há` ou `.waiting-time`; vazio apenas em `alert-info`. Em 1280 px as
  regiões são 8/4; em 390 px empilham, com wrap, alvo ≥44 px e sem overflow.
- **R6 — Preservação.** Guards por subtipo, estados da aba, ordenação, paginação,
  partial/polling, busca, links read-only e query count sem N+1 permanecem;
  pendentes de OLI-001 não sofre alteração visual/comportamental neste slice.

### Matriz requisito → evidência planejada

| Req. | Evidência |
| --- | --- |
| R1 | testes de título/filtro/container + jornada decididos |
| R2 | fixture com rows decididas, labels/disposições/horários + PNG |
| R3 | teste específico aprovado + negado e ausência de resultado agregado |
| R4 | teste de estrutura/link/read-only + medidas 8/4 |
| R5 | asserts de ausência de espera + vazio + medidas mobile/overflow |
| R6 | suíte de fila/guards/partial/paginação/query count + regressão de pendentes |

## 5. Expected blast radius

```yaml
expected_files:
  - templates/doctor/queue.html
  - templates/doctor/_queue_content.html
  - apps/doctor/views.py
  - static/css/app.css
  - apps/doctor/tests/test_queue.py
  - apps/doctor/tests/test_queue_parity.py
  - apps/doctor/tests/test_page_titles.py
  - .pi/skills/verify-hmd/features/fila-medica-decididos.md
  - .pi/skills/verify-hmd/features/README.md
  - .pi/skills/verify-hmd/scripts/browser/drive-fila-medica-decididos.mjs
allowed_incidental_files:
  - driver/feature médica existente somente para correção de seletor compartilhado, com justificativa
out_of_scope:
  - static/js/doctor_queue_filter.js, salvo defeito comprovado na busca decidida
  - apps/doctor/urls.py, presenters.py, forms.py, services, models e migrations
  - templates/doctor/_case_cards.html, case_detail.html e decide.html
  - alterar o card pendente entregue em OLI-001
  - scheduler, NIR, painel e _nav (OLI-005)
```

**Preservar:** rows pré-carregadas, disposição/motivos/horários persistidos,
acesso read-only e totalidade das decisões independentes por procedimento.

## 6. Failing-before plan

Após escrever/adaptar os testes e antes de alterar produção:

```bash
uv run pytest \
  apps/doctor/tests/test_queue.py \
  apps/doctor/tests/test_queue_parity.py \
  apps/doctor/tests/test_page_titles.py -q
```

**Falhas esperadas:** título genérico, contagens de pendentes no filtro decidido,
card com `Recebido há`/`waiting-time`, disposição e horário ausentes, CTA textual
em vez de botão e ausência de badges distintos no caso misto. O RED deve incluir
um caso com um procedimento aprovado e outro negado. Registrar falhas reais do
contrato, não erros de fixture.

## 7. Implementation constraints

- Seguir `AGENTS.md`; menor mudança correta, sem reabrir OLI-001 ou antecipar
  OLI-004–005.
- Ler disposição e `doctor_decided_at` diretamente das rows já pré-carregadas;
  não consultar `decision_event`, trilha ou presenter do detalhe por card.
- Não alterar o universo de estados da aba nem criar recorte temporal.
- Não mostrar motivo da negativa no card se isso aumentar densidade além do
  aprovado; motivos continuam no detalhe.
- CSS somente no escopo da lista; qualquer ajuste compartilhado deve manter
  pendentes e demais superfícies inalterados.
- Nova jornada usa somente dados sintéticos e o backend/settings de verificação;
  não criar endpoint de mutação para prova.

## 8. Validation

**Focused validation**

- RED da seção 6 e mesmo conjunto verde após implementação.
- Teste de decisões mistas, horários, CTA/read-only, filtro sem contagens,
  estados, ordenação, paginação/partial e query count.
- Regressão dos testes e da jornada de pendentes de OLI-001.

**Project-required gates**

- Quality gate completo de `AGENTS.md` §2, com resultados registrados.

**Runtime/verification evidence**

- Criar e executar `fila-medica-decididos` pelo padrão `verify-hmd`, com login
  real, instância própria e cleanup.
- Cenário sintético com aprovação + negação: PNG/snapshot/JSON em 1280 e 390 px,
  disposição/horários por row e CTA «Ver detalhes».
- Medidas 8/4, CTA à direita, alvo ≥44 px, empilhamento e ausência de overflow;
  prova de zero `.waiting-time` e de vazio em alerta info.
- Comparar com o molde de decididos da referência quando disponível, sem copiar
  campos CHD.

## 9. Acceptance criteria

- [ ] R1–R6 possuem evidência correspondente.
- [ ] Caso misto mostra aprovação e negação por procedimento, sem agregação.
- [ ] Cada row decidida exibe disposição e data/hora; não há espera/recebimento.
- [ ] «Ver detalhes» é CTA real e abre o detalhe read-only autorizado.
- [ ] Desktop/mobile, vazio, filtro e ausência de overflow estão nas evidências.
- [ ] Pendentes, guards, ordenação, paginação, polling e queries não regrediram.
- [ ] Quality gate e jornadas pendentes/decididas terminam com exit 0.
- [ ] Relatório e revisão independente estão concluídos antes da revisão do dono.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se o resultado exigir alterar estados/recorte
da aba, event source ou regra de decisão; se horários/disposições não estiverem
nas rows; se houver necessidade de nova query por card, migration ou alteração
no detalhe; ou se preservar pendentes exigir mudança de escopo não aprovada.

## 11. Evidence report

**Caminho:** `/tmp/hmd-slice-oli-003-report.md`

Contém status, R1–R6 → evidência, arquivos, RED/passing-after, comandos/exit
status, run/artefatos da nova jornada, medidas, cenário misto, regressão de
pendentes, blast radius, riscos e decisões pendentes.

## 12. Worker handoff

Implement only this approved slice. Reconstruct context from the listed files;
do not rely on prior conversation. Follow `AGENTS.md`, produce failing-before
evidence, implement the smallest correct change and run focused/project/runtime
validation. Do not update `tasks.md`, commit, push, merge, archive, or start
another slice. If a new decision is required, stop with
`BLOCKED_NEEDS_DECISION` and evidence. Otherwise finish with
`READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-slice-oli-003-report.md`.
