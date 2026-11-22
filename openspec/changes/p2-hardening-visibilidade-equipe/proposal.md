# Proposal

## Why

A change `visibilidade-por-equipe` (7/7 slices aceitos, 2026-11-22) deixou 3 P2s formais de reviewer e 2 notas de engenharia/risco deferidas pelo protocolo `pi-subagents-openspec-loop/2` (P2 não reabre slice verde). Nenhum é defeito de comportamento — são limpeza de código, lacuna de cobertura de teste e precisão de documentação. Esta micro-change os quita em dois slices enxutos, no molde do precedente `tests-hardening-paridade-ats` (skip_specs).

## What Changes

- **Limpeza de código (sem mudança de comportamento)**: remover o parâmetro `user` morto de `_decided_today_cases` (`apps/doctor/views.py`) e convergir o re-check de papel inline de `confirm_case_procedure_review` ao helper `_assert_nir_role` (`apps/intake/services.py`), parameterizando o helper para preservar as mensagens de erro específicas por ação.
- **Cobertura e precisão**: asserção da negação alheia no partial de «Processados Hoje» (`test_processed_today.py`); teste dedicado do gate de PDF em `AWAITING_NIR_ACK`; correção do manual do agendador («a ninguém» → restrição é da equipe de agendamento, com menção ao gate de estados permitidos).

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

(nenhuma — `skip_specs: true`: nenhum requisito spec-level muda; todos os itens são contratos já promovidos/promovíveis pela change original, aqui apenas Quitados no código/teste/doc)

## Impact

- `apps/doctor/views.py` (parâmetro morto + call sites)
- `apps/intake/services.py` (helper parameterizado + convergência do re-check inline)
- `apps/scheduler/tests/test_processed_today.py` (1 asserção no partial)
- `apps/scheduler/tests/test_detail_cards.py` ou arquivo irmão (teste AWAITING_NIR_ACK)
- `templates/accounts/manual.html` (2 frases da seção Agendador) + marcadores do `test_manual.py` se citarem o texto
- **Fora de escopo**: notificações por equipe (backlog de produto do dono), qualquer mudança de comportamento/spec/FSM/migration.
