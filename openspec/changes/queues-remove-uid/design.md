# Design: queues-remove-uid

## Contexto

- `templates/intake/my_cases.html:38`, `templates/doctor/queue.html:52`,
  `templates/scheduler/queue.html:30`: `<span class="font-monospace small
  text-muted">Caso {{ …case_id }}</span>` — uid completo no cabeçalho do
  card.
- O nº de ocorrência já aparece nos 3 cards («Nº de ocorrência: <n>» ou
  `—`); nome/idade também (changes recentes).

## D1 — Remoção simples, sem substituto

O span sai inteiro dos 3 cards. Nenhum elemento novo: a linha «Nº de
ocorrência» (com `—` quando ausente) e a identificação do paciente
permanecem. Caso sem ocorrência (retido no gate): o card mostra paciente
(nome/idade quando presentes) e status — **sem fallback para uid**
(decisão do dono, opção (a)).

## D2 — Contrato pinnado nas specs

Cada requirement de fila/meus-casos ganha frase explícita («os cards NÃO
exibem o identificador interno do caso (uid)») + cenário de ausência —
evita reintrodução futura por inércia de template copiado.

## D3 — Não-mudanças

- Painel (fallback uid curto na ausência de ocorrência — decisão própria
  vigente), detalhes de caso, URLs/rotas (o uid segue como chave técnica
  nas rotas), filtros/ordenação das filas.
- Nenhuma view/model/migration — mudança estritamente de template+tests.
