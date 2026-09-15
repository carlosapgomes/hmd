# Slice 001 — Remoção do uid dos cards das filas

```yaml
expected_files:
  - templates/intake/my_cases.html
  - templates/doctor/queue.html
  - templates/scheduler/queue.html
  - apps/intake/tests/test_my_cases.py
  - apps/doctor/tests/test_queue.py
  - apps/scheduler/tests/test_views.py
```

## Contexto necessário

- Os 3 templates renderizam no cabeçalho do card:
  `templates/intake/my_cases.html:38`
  (`Caso {{ item.case.case_id }}`), `templates/doctor/queue.html:52` e
  `templates/scheduler/queue.html:30` (`Caso {{ item.case_id }}`) — uid
  completo em `font-monospace small text-muted`.
- O nº de ocorrência já aparece nos 3 cards («Nº de ocorrência: <n>»,
  `—` quando ausente); nome/idade também (changes recentes).
- Decisão do dono (opção (a)): SEM fallback — caso sem nº de ocorrência
  fica apenas com a identificação do paciente (nome/idade quando
  presentes) e status.
- Testes existentes: alguns pinnam `Caso {{ uid }}` ou usam o uid para
  localizar o card no HTML (ver `test_queue*.py`/`test_my_cases.py`/
  `test_views.py` — os helpers que fatiam o HTML pelo `case_id` continuam
  válidos, pois o uid segue no `href` do link do card).

## Goal

Cards das 3 filas sem o uid; nº de ocorrência (com `—`) e identificação
do paciente como únicos identificadores.

## Deliverables

### R1 — Templates

- Remover o span «Caso <uid>» dos 3 templates. Nenhum elemento novo;
  nenhuma outra linha tocada.

### R2 — Testes (RED→GREEN)

- Novos (RED): em cada fila, o card do caso NÃO contém o uid do caso no
  TEXTO visível (assert escopado ao card — o uid segue no `href`, então o
  assert deve mirar o corpo do card, ex.: fatia entre `<a`…`</a>` sem o
  span removido, ou ausência de `Caso <uid>`; NÃO usar assert global de
  uid no HTML, que falharia pelo href); caso sem nº de ocorrência → `—`
  na linha de ocorrência e nome presente, sem uid em lugar nenhum do
  corpo.
- Atualizar pins existentes que referem «Caso <uid>» (se houver) para o
  contrato novo.
- Bateria completa do AGENTS.md (pytest alvo + suíte + ruff/format/mypy/
  `manage.py check`).

## Gates para o reviewer (2 linhas)

1. Diff dos templates: SÓ o span do uid removido (3 linhas; nenhum outro
   elemento/conteúdo tocado).
2. Testes de ausência escopados ao CORPO do card (não ao HTML inteiro — o
   uid permanece no href da rota, que é chave técnica, não exibição).

## Out of scope

- Painel (fallback uid curto vigente), detalhes de caso, rotas/urls,
  filtros/ordenação, qualquer view/model.
