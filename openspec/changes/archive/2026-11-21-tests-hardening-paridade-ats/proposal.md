# Proposal

## Why

A change `paridade-ats-filas-historico-intercorrencia` (slices 001–005 aceitos) deixou
dois P2s deferidos de **cobertura de teste** que valem a pena quitar antes do
arquivamento: (1) o teste de issue ativa da página «Histórico» do agendador fabrica o
estado em vez de exercitar o serviço real `open_post_closure_issue`, e falta o caso
`CLEANED` + PDF + mesmo decisor (cards ausentes + 404); (2) o backfill dos carimbos
`doctor_decided_*` (migration 0014) é testado chamando a função diretamente, mas sem o
fluxo sobre estado histórico pré-deploy com verificação na aba «Decididos Hoje» — o
cenário exato do dia de implantação. Mudança **somente de testes**: nenhum comportamento
de produção muda; por isso `skip_specs: true` (zero delta de spec).

## What Changes

- Teste do histórico do agendador exercitando `open_post_closure_issue` de verdade
  (confirmar → encerrar → reabrir pelo serviço): caso aparece no universo do histórico
  com issue ativa e carimbo `scheduling_decided_*` preservado (substitui o estado
  fabricado).
- Teste `CLEANED` + PDF + mesmo decisor na superfície histórica: cards de PDF/texto
  ausentes e endpoint respondendo 404.
- Teste do fluxo de backfill: seed de estado pré-deploy (decisão médica só em
  eventos/rows, sem carimbos) → execução do backfill da migration 0014 (padrão do
  projeto: função da migration contra dados semeados) → carimbos preenchidos com ator →
  decisão visível na aba «Decididos Hoje» do decisor certo (instantes derivados de
  `day_bounds`, determinísticos em qualquer hora de execução).

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

(nenhuma — `skip_specs: true`: mudança exclusivamente de testes, sem alteração de
comportamento observável)

## Impact

- **apps/scheduler/tests/test_historical_search.py** (+2 testes), **apps/doctor/tests/test_decided_today.py**
  (+1 teste de fluxo de backfill); possivelmente **apps/cases/tests/** (helper de seed
  se fizer sentido idiomático). Nenhum arquivo de produção, migration, template ou spec
  é alterado.
- Consolida para o arquivamento da change de paridade: P2 #2 (slice 004) e P2 #3
  (slice 005) quitos; P2 #1 (qualidade de RED da corrida) permanece como documentação,
  já registrada nas tasks do slice 003.
