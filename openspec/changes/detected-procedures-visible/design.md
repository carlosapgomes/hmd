# Design — detected-procedures-visible

## D1 — Tabela única por row (origem × detecção)

`CaseProcedure` já carrega os dois eixos: `declared_by_nir` (origem) e
`detection_status` (reconciliação; `PENDING` antes dela). Coincidente = UMA
row (`UniqueConstraint` caso+tipo; declarada e detectada); divergência =
rows distintas (declarada `not_detected` + detectada `declared_by_nir=False`).
A UI mostra todas as rows, sempre, com badges: «Declarado» (origem NIR),
«Detectado ✓» / «Não detectado» (após reconciliação; casos sem LLM ainda
mostram apenas a origem). Detectado não-declarado ganha destaque visual
(badge info). **Sem query adicional**: o detalhe do NIR já usa
`prefetch_related("procedures")` (intake); o detalhe do médico lê a relação
`case.procedures` no presenter (1 caso = 1 query, como hoje — `apps/doctor/views.py`
não pré-carrega; manter a leitura única existente, sem N+1 novo).

## D2 — NIR: tabela na seção + resumo no card de revisão

A seção «Procedimentos declarados» vira «Procedimentos do caso» (tabela D1).
O card «Revisão do gate — divergência» ganha, acima do botão Liberar, o
resumo em duas linhas: Declarados (com detecção) / Detectados na extração
(não declarados em destaque) — o contexto da decisão fica onde a ação vive.
Escopo de acesso inalterado (só o criador; decisão do dono na conversa).

## D3 — Médico: título e ordem clínica

O card passa a «Procedimentos do caso» (o título antigo mente quando exibe
row detectada não-declarada de bypass). A ordem clínica pinada na spec se
mantém — apenas o título muda; o cenário de ordem da spec doctor-decision
tem o CORPO atualizado (títulos verbatim preservados; corpos governam —
nota D5b do painel-ats-parity). O teste de ordem (find por títulos) é
atualizado no slice.

## Não-objetivos

Cards de fila/painel (continuam mostrando declarados — foco é a decisão);
mudanças em FSM/persistência; trilha de eventos.
