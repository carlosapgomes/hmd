# Proposal

## Why

`apps/doctor/tests/test_reappearance_wait.py::test_pending_card_uses_inherited_wait_in_html`
falha todos os dias entre 21:00 e 00:00 (America/Bahia): a pré-condição do teste
compara a **data UTC** de `created_at` (`incoming.created_at.date()`) com a
**data local** (`timezone.localdate()`). Nesse intervalo o UTC já está no dia
seguinte enquanto o fuso local ainda está no dia anterior. Foi o que quebrou o
gate de 25/11/2026 (suíte: 2869 passaram, 1 falhou), sem relação com a change em
validação. O projeto já trata essa fronteira como bug conhecido —
`apps/cases/day_bounds.py` documenta «usar `timezone.localdate()`, nunca a data
UTC de `timezone.now()`». Mudança **somente de teste**: nenhum comportamento de
produção muda; por isso `skip_specs: true` (zero delta de spec).

## What Changes

- A pré-condição do teste passa a comparar data local com data local:
  `timezone.localdate(incoming.created_at) == timezone.localdate()`. O corpo do
  teste (card e partial exibindo a espera herdada de 5 dias) não muda.

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

(nenhuma — `skip_specs: true`: mudança exclusivamente de teste, sem alteração de
comportamento observável)

## Impact

- `apps/doctor/tests/test_reappearance_wait.py` (1 linha). Nenhum arquivo de
  produção, migration, template ou spec é alterado.
- Busca por `localdate` em testes: é o único ponto do repositório que compara a
  data de um datetime aware com `timezone.localdate()`; os demais já usam
  `timezone.localdate(<aware>)`.
