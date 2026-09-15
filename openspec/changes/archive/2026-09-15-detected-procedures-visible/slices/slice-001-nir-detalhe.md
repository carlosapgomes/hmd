# Slice 001 — Detalhe do NIR: procedimentos declarados × detectados

## Expected files

- `apps/intake/views.py` (apenas o detalhe do caso: presenter de rows)
- `templates/intake/case_detail.html` (seção de procedimentos + card de revisão)
- `apps/intake/tests/test_case_detail.py` (ou o módulo de testes do detalhe existente)

## Requisitos

### R1 — presenter de rows

Função pura no views (ou módulo existente) que transforma
`case.procedures` (prefetch) em rows: `label` (catálogo, fallback tipo
bruto), `is_declared` (declared_by_nir), `detection` ∈
{'detected','not_detected',None} ANTES da reconciliação (status inicial das
rows: usar o valor real do campo; se o caso ainda não passou pela
reconciliação, exibir apenas a origem). Context do template recebe
`procedure_rows` (substitui/complementa `procedure_labels` — manter
`procedure_labels` se outros templates usam; detalhe usa rows).

### R2 — template

- Seção «Procedimentos declarados» → «Procedimentos do caso»: uma linha por
  row com label + badge origem («Declarado» secundário) + badge detecção
  («Detectado» sucesso / «Não detectado» warning) quando houver
  reconciliação; row detectada não-declarada: badge «Detectado na extração»
  info.
- Card «Revisão do gate — divergência»: acima do botão Liberar, resumo:
  «Declarados: <labels com detecção>» e «Detectados na extração:
  <labels não-declarados>» (se houver); casos sem rows → texto atual.

### R3 — testes (TDD, RED→GREEN)

- Divergência (caso com angio_art_perif declarada not_detected + art_perif
  detectada declared_by_nir=False): detalhe do NIR criador mostra AMBOS os
  labels + badges («Não detectado», «Detectado na extração») e o resumo no
  card de revisão.
- Coincidente (uma row declarada detected): badges «Declarado» +
  «Detectado».
- Caso sem reconciliação (NEW/PDF_EXTRACTING): só origem, sem badge de
  detecção.

## Gates

- NÃO modificar FSM/persistência; sem query extra (usar rows do prefetch).
- Não tocar em meus-cases (lista) nem nos templates de fila.
- `git diff` limitado aos expected files.
