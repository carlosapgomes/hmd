# Slice 002 — Detalhe do médico: procedimentos do caso com detecção

## Expected files

- `apps/doctor/presenters.py` (rows do detalhe: origem+detecção)
- `templates/doctor/case_detail.html` (card de procedimentos)
- `apps/doctor/tests/test_detail.py`

## Requisitos

### R1 — presenter

O contexto `declared` do detalhe passa a rows completas: label, subtype,
`is_declared`, `detection`, disposition_label (atuais) — mesmas regras do
slice 001 (label do catálogo com fallback; detecção ausente antes da
reconciliação). Sem query extra (rows já pré-carregadas pelo detalhe).

### R2 — template

Card «Procedimentos declarados» → «Procedimentos do caso»: cada row exibe
label + subtype + badges origem/detecção (como D1) + «Disposição:»
(atual). Row detectada não-declarada (bypass) aparece com badge «Detectado
na extração». Ordem dos cards inalterada (identificação → procedimentos →
quadro clínico → alertas).

### R3 — testes (TDD, RED→GREEN)

- Atualizar o pin de ordem dos títulos (novo título «Procedimentos do
  caso» na mesma posição).
- Caso AWAITING_DOCTOR coincidente: row única com «Declarado» +
  «Detectado» + disposição.
- Caso com bypass (declarada not_detected + detectada extra): detalhe
  médico exibe a row detectada não-declarada.

## Gates

- Reverter a implementação do presenter isoladamente deve derrubar os
  testes novos (prova de discriminação).
- Sem tocar na fila médica nem nas decisões (POST); `git diff` limitado
  aos expected files.
