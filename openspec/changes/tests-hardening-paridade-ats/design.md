# Design

## Context

Dois P2s deferidos dos reviews da change `paridade-ats-filas-historico-intercorrencia`
(review 1 do slice 004 e reviews 1–2 do slice 005) apontaram lacunas de cobertura em
comportamentos JÁ aceitos e commitados. Esta micro-change os quita com testes — zero
mudança de produção.

## Goals / Non-Goals

**Goals**

- G1: histórico do agendador testado pelo caminho real (serviço de reabertura
  pós-encerramento) em vez de estado fabricado; caso `CLEANED` + PDF + mesmo decisor
  coberto (cards ausentes + 404).
- G2: fluxo do backfill da migration 0014 testado sobre estado pré-deploy semeador,
  com verificação final na aba «Decididos Hoje» do decisor certo.

**Non-Goals**

- Qualquer mudança de produção, migration, template ou spec.
- P2 #1 da change de paridade (qualidade de RED do teste da corrida) — permanece
  resolvido como documentação (nota nas tasks do slice 003).
- Novos helpers/frameworks de teste (sem dependências novas; sem django-test-migrations).

## Decisions

### D1 — Failing-before é N/A; evidência é o pin de regressão

Os testes desta change pinam **comportamento já aceito** (guard do slice 002, universo
do 004, backfill do 005). Um RED genuíno exigiria quebrar produção deliberadamente —
o mesmo racional registrado para a corrida do slice 003 (P2 #1). O contrato do slice
exige, em vez de RED: (a) cada teste novo exercitar o **serviço/função real** (nada de
estado fabricado para os comportamentos-alvo), e (b) suíte completa verde.

### D2 — Fluxo de backfill segue o padrão de teste de migration do projeto

Precedente do projeto (quirk documentado no `PROJECT_CONTEXT.md`: flush de
`TransactionTestCase` + `--reuse-db` apaga seeds de migration): o teste semeia o estado
pré-deploy (decisão médica registrada APENAS em `CaseEvent` + `CaseProcedure`, com
`doctor_decided_*` nulos), executa a **função de backfill da migration 0014** contra
esse estado e então verifica (1) carimbos preenchidos com ator correto e (2) a decisão
listada na aba do decisor. Sem `--create-db` extra (a migration 0014 já está aplicada no
banco de teste; o que se exerce é a função de dados sobre estado histórico — mesmo
formato do teste existente, agora com a verificação de fila que faltava).

### D3 — Determinismo temporal herdado

Instantes de decisão nos testes novos derivam de `apps/cases/day_bounds.py`
(`start+2h`-style), nunca `now−Nh` — lição do P1 do slice 005 (testes quebravam antes
das 05:00 locais).

## Risks / Trade-offs

- [Teste de migration por função não cobre o plano de migração em si] → limitação
  conhecida e aceita pelo padrão do projeto; o risco residual (wiring da migration) é
  baixo porque a 0014 já aplicou em dev/teste sem incidente.
- [Seed pré-deploy divergence do estado real de produção] → o seed replica o formato
  canônico (eventos + rows), que é a única fonte existente antes da 0014.

## Migration Plan

Nenhuma migration. Deploy: nenhum (testes apenas).

## Open Questions

Nenhuma.
