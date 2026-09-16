# Slice 001 — Página de trabalho única do médico

## Objetivo

A decisão médica passa a acontecer numa **página única**: a fila leva direto
à página de trabalho (`doctor:case_decide`) com toda a leitura do caso e o
formulário de decisão como **último bloco**, âncora «Ir para o formulário de
decisão» no topo e [Registrar decisão] + [Sair sem decidir] juntos ao fim.
Sem reserva/lock neste slice (vem no 002).

## Contexto necessário (leia antes)

- `openspec/changes/case-work-lock/design.md` — seções D1 e D3 (botões).
- `apps/doctor/views.py` — `case_decide` (GET renderiza
  `build_case_detail_context(case)` + `DoctorDecisionForm`; hoje template
  `decide.html` só com o formulário).
- `templates/doctor/case_detail.html` — detalhe read-only com todos os cards
  (Identificação, Procedimentos do caso, Sumário clínico, Estrutura extraída,
  Decisões registradas, Alertas consultivos, Recomendação agregada do
  pipeline, Requisitos gerais acionáveis, Prior-case, Documentos, Anexos).
- `templates/doctor/decide.html` — formulário atual (94 linhas).
- `templates/doctor/queue.html` — fila; card da aba aguardando linka
  `case_detail` (l. ~50).
- `apps/doctor/tests/test_detail.py` — pins de ordem/títulos
  (`_CLINICAL_ORDER`, lista de títulos dos cards ~l.598) e
  `_procedures_card`; `apps/doctor/tests/test_decision.py` — testes da view.
- Helpers de teste: `apps/doctor/tests/conftest.py` (`client`, `user_factory`,
  `_login`); **os testes novos de página única vivem em `test_decision.py` e
  usam os helpers LOCAIS desse módulo** (duplicados lá em relação ao
  `test_detail.py` — verificar os nomes existentes no topo do arquivo antes
  de criar; não importar de `test_detail.py`).

## Requisitos verificáveis

- **R1** Fila médica (aba aguardando): ação do card leva a `doctor:case_decide`
  (não ao detalhe).
- **R2** Página de trabalho renderiza, em ordem única: Identificação →
  Procedimentos do caso → Sumário clínico → Estrutura extraída → Prior-case →
  Documentos → Anexos → Requisitos gerais acionáveis → Recomendação agregada
  do pipeline → Alertas consultivos → **formulário de decisão** (`id="
  doctor-decision-form"`, último bloco).
- **R3** «Decisões registradas» NÃO aparece na página de trabalho (permanece
  no detalhe read-only).
- **R4** Primeiro card tem âncora «Ir para o formulário de decisão» apontando
  para `#doctor-decision-form`.
- **R5** Ao fim do formulário: [Registrar decisão] (submit) e «Sair sem
  decidir» (link para `doctor:queue` neste slice).
- **R6** Detalhe read-only (`case_detail`) inclui os mesmos cards (mesmo
  partial, mesma ordem) + «Decisões registradas».

## Escopo e expected blast radius

```yaml
expected_files:
  - templates/doctor/_case_cards.html      # novo partial extraído do case_detail
  - templates/doctor/decide.html           # página de trabalho completa
  - templates/doctor/case_detail.html      # passa a incluir o partial
  - templates/doctor/queue.html            # ação do card da aba aguardando
  - apps/doctor/tests/test_detail.py       # pins de ordem/títulos
  - apps/doctor/tests/test_decision.py     # testes novos (página única)
  - apps/doctor/tests/test_queue.py        # R1: teste novo + teste existente
                                           # test_queue_items_link_to_case_detail
                                           # (l. ~229) atualizado para case_decide

allowed_incidental_files: []

out_of_scope:
  - qualquer lógica de lock/reserva (slice 002)
  - work_lock.js / endpoints renew/release / case_leave (slice 003)
  - scheduler (slice 004)
  - intake, presenters.py, views.py do doctor (não precisa mudar neste slice)
```

Extração do partial: mover os blocos de card de leitura de
`case_detail.html` para `_case_cards.html` **sem** edição de conteúdo
(«Decisões registradas» fica fora do partial, no template do detalhe).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) | Teste/check |
| --- | --- | --- |
| R1 | `templates/doctor/queue.html`, `test_queue.py` | `test_queue.py::test_awaiting_card_links_to_decide` (novo; assert endurecido `0 < found` de `doctor:case_decide` no card e `case_detail` ausente da ação do card) + atualizar `test_queue_items_link_to_case_detail` existente (aba aguardando → `case_decide`; aba decididos segue `case_detail`) |
| R2 | `decide.html`, `_case_cards.html` | `test_decision.py::test_work_page_orders_reading_then_form` (novo; posições: alertas < form; form após todos os títulos de leitura) |
| R3 | `decide.html` | `test_decision.py::test_work_page_has_no_recorded_decisions_block` (novo) |
| R4 | `decide.html` | `test_decision.py::test_work_page_has_form_anchor` (novo; `href="#doctor-decision-form"` e `id="doctor-decision-form"` — ambos com `0 < pos`) |
| R5 | `decide.html` | `test_decision.py::test_work_page_footer_buttons` (novo) |
| R6 | `case_detail.html` | `test_detail.py` — pins existentes continuam verdes (mesma ordem) |

## RED

- Comando: `TEST_DB_PORT=5433 uv run pytest -q apps/doctor/tests/test_decision.py -k "work_page"`
- Falha esperada: os novos testes falham porque `decide.html` não renderiza
  os cards de leitura (nem âncora/botões): `assert 'Sumário clínico' in
  body` → False etc. — e NÃO por erro de setup (status 200 alcançado).

## GREEN / verificação local

- `TEST_DB_PORT=5433 uv run pytest -q apps/doctor/tests/test_decision.py` — exit 0
- `TEST_DB_PORT=5433 uv run pytest -q apps/doctor/tests/test_detail.py apps/doctor/tests/test_queue.py` — exit 0
- `uv run ruff check apps/doctor && uv run mypy apps/doctor` — exit 0 (sem py novo relevante, vale check dos templates via suite)

## Critérios de aceitação

- [ ] R1–R6 demonstrados pelos testes listados (RED→GREEN)
- [ ] Partial sem duplicação de marcação (case_detail inclui `_case_cards`)
- [ ] Nenhum arquivo fora do blast radius
- [ ] Sem commits/tasks.md (parent faz)
