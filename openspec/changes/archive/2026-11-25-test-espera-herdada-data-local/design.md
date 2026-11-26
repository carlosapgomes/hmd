# Design

## Context

Motivação em `proposal.md` — Why. A linha 80 do teste é uma pré-condição de
sanidade («o caso novo foi criado hoje; a espera exibida vem do caso herdado, não
da criação de hoje»), não o comportamento sob teste. O produto calcula a espera
a partir de `effective_wait_started_at` e não depende da data UTC.

## Goals / Non-Goals

**Goals**

- G1: o teste deixa de depender do horário do relógio.

**Non-Goals**

- Qualquer mudança de produção, migration, template ou spec.
- Reescrever a pré-condição ou o restante do teste.

## Decisions

### D1 — Corrige-se o teste, não o produto

`created_at` é aware em UTC; `.date()` devolve a data UTC. A forma idiomática do
projeto é `timezone.localdate(<aware>)` (usada em `apps/cases/test_day_bounds.py`
e nos testes do dashboard). Alternativa considerada: `timezone.localtime(...).date()`
— equivalente, mas `localdate` é a já adotada no repositório.

### D2 — RED é o relógio real; a verificação usa os dois lados da fronteira

O RED genuíno só existe no intervalo 21:00–00:00 (-03). Evidências: (a) execução
real dentro do intervalo (falha antes, passa depois); (b) para não depender do
horário do revisor, o teste corrigido é executado também com
`django.utils.timezone.now` fixado em um instante após a fronteira UTC
(22:00 local) e em um instante antes (20:30 local), com plugin pytest
descartável fora do repositório — verde nos dois.

### D3 — Escopo de uma linha

Varredura por `localdate(` em `apps/*/tests` e `tests/`: nenhum outro teste
compara data UTC com data local. Não há classe de bug a tratar além desta linha.

## Risks / Trade-offs

- [A pré-condição deixa de pegar «criado em outro dia» no sentido UTC] → não é
  o que ela verifica; o sentido relevante é o dia local, o mesmo do produto.

## Migration Plan

Nenhuma. Apenas teste.
