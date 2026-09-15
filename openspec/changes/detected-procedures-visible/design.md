# Design — detected-procedures-visible

## D1 — Tabela única por row (origem × detecção)

`CaseProcedure` já carrega os dois eixos: `declared_by_nir` (origem) e
`detection_status ∈ {detected, not_detected, pending?}` (reconciliação).
Coincidente = UMA row (declarada e detectada); divergência = rows distintas
(declarada `not_detected` + detectada `declared_by_nir=False`). A UI mostra
todas as rows, sempre, com badges: «Declarado» (origem NIR), «Detectado ✓» /
«Não detectado» (após reconciliação; casos sem LLM ainda mostram apenas a
origem). Detectado não-declarado ganha destaque visual (badge info). Sem
query extra: rows já vêm do `prefetch_related("procedures")` existente nos
dois detalhes.

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
