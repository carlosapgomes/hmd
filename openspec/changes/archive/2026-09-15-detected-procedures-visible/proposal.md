# detected-procedures-visible — procedimentos declarados × detectados visíveis nos detalhes

## Por quê

Caso real em dev (`dd405e6d`): divergência `angio_art_perif` declarada ×
`art_perif` detectada reteve o caso para o NIR, mas o detalhe listava apenas
os declarados — o NIR decide «Liberar caso» às cegas. O dono pediu: os
procedimentos **detectados** devem aparecer **sempre**, inclusive quando
coincidem com os declarados (detalhe do médico incluso). É lacuna de spec
(nenhuma spec exige exibir detectados) e de UI.

## O que muda

- Detalhe do NIR: seção de procedimentos exibe TODAS as rows do caso com
  origem (Declarado/Detectado) e status de detecção; no card de revisão por
  divergência, resumo declarados × detectados junto à ação.
- Detalhe do médico: card «Procedimentos declarados» passa a
  «Procedimentos do caso» com as mesmas rows (detectado não-declarado de
  bypass incluso; coincidente = uma row com ambos os badges).

## Impacto

- Specs: `intake-nir` e `doctor-decision` (MODIFIED, +1 cenário cada).
- Código: `apps/intake/views.py` + `templates/intake/case_detail.html`;
  `apps/doctor/presenters.py` + `templates/doctor/case_detail.html`.
- Sem migrations; sem mudança de FSM/persistência (dados já existem em
  `CaseProcedure.declared_by_nir`/`detection_status`).
