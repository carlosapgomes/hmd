# Tasks: ui-parity-queues

## 1. Fila médica

Contract: `slices/slice-001-doctor-queue.md`

- [x] 1.1 Cards `case-card + summary-box + pulse-dot + waiting-time`,
  abas `Pendentes/Decididos`, badges de contagem; RED: testes de
  marcadores/classes falham no HTML atual; GREEN: novo visual;
  verificar com `pytest` + capturas vs ats-web
- [x] 1.2 Endpoint `doctor:queue_partial` (mesmo fragmento do
  server-side, mesmos guards) + htmx 20 s com reaplicação de
  busca/filtro pós-swap; verificar com teste do fragmento + jornada
  do harness (novo caso compatível aparece sem reload; caso fora
  do filtro ou da página não aparece)
- [x] 1.3 Busca client-side (nome/ocorrência) + filtro de subtipo
  (server autoritativo, client refina); verificar com testes server +
  jornada do harness + `assertNumQueries` sem regressão

## 2. Fila do agendador

Contract: `slices/slice-002-scheduler-queue.md`

- [x] 2.1 Mesmo padrão visual e abas `Pendentes/Processados` com
  contadores (remove o bloco legado `nav-tabs`; o `_nav` do change 1
  permanece); verificar com `pytest` + capturas vs ats-web
- [x] 2.2 Endpoint `scheduler:queue_partial` + htmx 20 s; verificar
  com teste do fragmento + jornada do harness (caso compatível
  na página exibida aparece sem reload; fora dela, não aparece)
- [x] 2.3 Busca client-side; estados de reserva refletidos nos cards
  do partial; verificar com testes + jornada (lock de outro → sem
  ação; próprio → Continuar)

## 3. Meus casos (NIR)

Contract: `slices/slice-003-my-cases.md`

- [ ] 3.1 Visual de cards + abas `Ativos/Encerrados` com contadores,
  escopo por criador intacto; verificar com `pytest` + capturas
- [ ] 3.2 Filtros server-side (`q` ≥3 chars, status, tipo) com AND
  (sem paginação nesta view); verificar com testes de query
- [ ] 3.3 Partial + auto-refresh 20 s; verificar com teste do
  fragmento + jornada do harness (novo caso compatível aparece sem
  reload, aba e filtros preservados)
