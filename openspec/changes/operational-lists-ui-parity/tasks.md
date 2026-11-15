# Tasks: operational-lists-ui-parity

## 1. Slice 001 — Pendentes do médico

Contract: `slices/slice-001-doctor-pending-page-parity.md`

- [x] 1.1 Escrever RED de container/título, filtro único com contagens, estrutura 8/4, espera única, CTA/locks, demografia e estado vazio; executar os testes focados e registrar as falhas esperadas.
- [x] 1.2 Implementar página, card, CSS escopado, projeção de demografia e busca sem o seletor client-side redundante; verificar testes de estrutura, filtro, busca, partial, locks, paginação e queries verdes.
- [x] 1.3 Atualizar a jornada `fila-medica-busca-refresh`; comprovar 1280/390 px, medidas geométricas, alvo ≥44 px, ausência de overflow, filtro server-side e busca preservada no polling, com PNG/snapshot/JSON.
- [x] 1.4 Rodar o quality gate, produzir `/tmp/hmd-slice-oli-001-report.md` e submeter o slice à revisão independente e humana antes de avançar.

## 2. Slice 002 — Pendentes do agendador

Contract: `slices/slice-002-scheduler-pending-page-parity.md`

- [x] 2.1 Escrever RED de container/título/alerta, filtro server-side `procedure_type`, estrutura 8/4, espera única, decisões por procedimento e ações de reserva; registrar as falhas antes da implementação.
- [x] 2.2 Implementar filtro validado antes da paginação, propagação ao partial/paginação, projeção das decisões e novo card/página com CSS escopado; verificar valor inválido, fora-da-página, locks, queries e aba processada preservada.
- [x] 2.3 Atualizar a jornada `fila-agendamento-busca-refresh`; comprovar 1280/390 px, medidas, decisões, filtro + busca + polling, locks e ausência de overflow com PNG/snapshot/JSON.
- [x] 2.4 Rodar o quality gate, produzir `/tmp/hmd-slice-oli-002-report.md` e submeter o slice à revisão independente e humana antes de avançar.

## 3. Slice 003 — Decididos do médico

Contract: `slices/slice-003-doctor-decided-page-parity.md`

- [x] 3.1 Escrever RED do título/filtro, card 8/4 orientado ao resultado, disposição e data/hora por procedimento, ausência de espera e CTA «Ver detalhes»; incluir caso misto aprovado + negado e registrar as falhas.
- [x] 3.2 Implementar a projeção das rows sem query por card e o card/estado vazio/mobile, mantendo a aba pendente do slice 001 e todos os guards/partials; verificar decisões mistas, demografia, queries e navegação read-only.
- [x] 3.3 Criar a jornada `fila-medica-decididos` e seu driver; comprovar resultado misto, horários, medidas 1280/390 px, CTA, ausência de espera e overflow com PNG/snapshot/JSON.
- [x] 3.4 Rodar o quality gate, produzir `/tmp/hmd-slice-oli-003-report.md` e submeter o slice à revisão independente e humana antes de avançar.

## 4. Slice 004 — Meus casos do NIR

Contract: `slices/slice-004-nir-my-cases-page-parity.md`

- [x] 4.1 Escrever RED de container/título, navegação sem duplicação com filtros preservados, cabeçalho neutro, card 4/3/3/2 sem resumo/espera, exceções e estado vazio; registrar as falhas antes da implementação.
- [x] 4.2 Implementar página/lista/cards, links do header com `filter_qs`, CSS escopado e CTA «Ver detalhes», preservando escopo, filtros AND, partial, sufixo, retenção e encerramento administrativo; verificar testes focados e regressões do detalhe.
- [x] 4.3 Atualizar a jornada `meus-casos-filtros`; comprovar troca de abas pelo header, filtros + polling, quatro regiões em 1280 px, empilhamento/CTA full-width em 390 px, exceções e ausência de overflow com PNG/snapshot/JSON.
- [x] 4.4 Rodar o quality gate, produzir `/tmp/hmd-slice-oli-004-report.md` e submeter o slice à revisão independente e humana antes de avançar.

## 5. Slice 005 — Navegação operacional e integração

Contract: `slices/slice-005-operational-nav-parity.md`

- [ ] 5.1 Escrever RED das pills ativas/hover/focus, contadores exclusivamente por `data-count`/pseudo-elemento, zero oculto e ausência de badges inline; verificar falhas sem alterar rotas/labels/contagens.
- [ ] 5.2 Implementar CSS HMD e partials `_nav` de NIR/médico/agendador, preservando `aria-current`, filtros do NIR e acesso por papel; verificar testes de navegação e identidade reservada.
- [ ] 5.3 Atualizar `header-nav-4-papeis` e comprovar desktop/mobile; executar a regressão das quatro jornadas de listas, conferir medidas e ausência de contadores duplicados/overflow com PNG/snapshot/JSON.
- [ ] 5.4 Rodar quality gate e mapa `verify-hmd` aplicável completo, produzir `/tmp/hmd-slice-oli-005-report.md` com a matriz dos cinco slices e submeter o change à revisão final; não arquivar nem iniciar outro change.
