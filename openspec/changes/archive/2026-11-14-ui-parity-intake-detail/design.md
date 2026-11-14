# Design: ui-parity-intake-detail

## Context

Ver proposal.md (Why). Estado atual: `intake/home.html` com inputs
nativos (`documents` + `attachments` + 13 radios de `procedure_type`);
`intake-upload.js` com 51 linhas; detalhe/decisão/confirmação e
painel no layout antigo (a casca, as fontes e o `_nav` já são do
padrão). Moldes: `upload.js` (632 linhas), `procedure_combobox.js`
(421 linhas), `decision.html` (layout 2 colunas), seletor de período
do dashboard e `_queue_content` do ats-web. Harness `verify-hmd` com
mapa parcial.

## Goals / Non-Goals

- Goals: paridade de apresentação e interação das 4 superfícies,
  adaptando os moldes aos names/limites/semântica HMD.
- Non-Goals: qualquer mudança de validação, FSM, catálogo, decisão
  atômica, métricas; paridade funcional de métricas; upload
  suplementar pós-criação (fora de escopo no HMD, existe no ats-web).

## Decisions

- **D1 — Upload adaptado, não copiado.** `static/js/intake-upload.js`
  reescrito sobre o molde: field names HMD (`documents`,
  `attachments`), limites HMD, lote homogêneo (1 PDF = 1 caso;
  anexos só com 1 relatório — regra atual preservada no JS), seção
  de anexos do change 10 intacta. O HMD **não** tem gate de
  confirmação de revisão (só o ats-web bloqueia submit por ele) e
  o porte **não** adiciona nenhum: anexo submete sem confirmação
  nova (com teste). Preview (lista, contagem, tamanho total) e
  drag-and-drop como no ats-web. `CorrectedResubmissionForm` herda
  o widget (1 PDF): script carregado também nessa página, testado
  nos dois fluxos.
- **D2 — Combobox com progressive enhancement.** `procedure_type`
  vira `ChoiceField` com select canônico (submetido sempre); o JS
  adiciona input/listbox/chevron e esconde o select (mesma técnica
  do ats-web). Busca restrita a labels + códigos canônicos dos 13
  perfis (sem sinônimos: o catálogo não tem aliases e este change
  não altera o catálogo — sinônimos aprovados ficam para change
  futuro com decisão de produto); sem JS, o select funciona.
  Validação e catálogo inalterados.
- **D3 — Detalhe/decisão/confirmação: só layout.** Tabelas de
  identificação, blocos de relatório, badges e cards no padrão
  ats-web; formulários, ordem dos cards clínicos (change
  `doctor-detail-context`), guards e atomics intactos. Nenhum
  comportamento de `decision.js` portado (decisão do dono, Q6).
- **D4 — Painel: só visual.** Toolbar de período, cards de métrica
  e lista em `case-card` com as queries e filtros atuais do HMD;
  contagens e regras de fonte imutável intactas.
- **D5 — verify-hmd estendido de forma limitada.** 4 jornadas
  (`upload-dragdrop`, `combobox-procedimento`, `detalhe-papeis`,
  `painel-visual`); sem mudar launch/doctor.
- **D6 — Reconciliação de spec sem mudança de código.** Débito
  pré-existente: as specs `intake-nir`/`attachments` descreviam
  atomicidade total e anexos com multi-PDF, mas o comportamento
  vigente (aprovado e testado) é 1 PDF = 1 caso com erro nomeado
  por arquivo e anexos só com 1 relatório. Este change reconcilia
  os textos com o código; nenhum `.py` de negócio muda por causa
  disso.

## Risks / Trade-offs

- [Risco] `upload.js` é o porte JS mais complexo (estado de lote,
  previews, anexos, confirmação) → Mitigação: adaptar por
  comportamento observável com a matriz de fixtures do change 10;
  sem JS o form nativo continua submetendo.
- [Risco] Combobox quebra acessibilidade → Mitigação: mesmo padrão
  ARIA do ats-web (listbox, `aria-expanded`, hint persistente);
  navegação por teclado testada na jornada.
- [Risco] Reenvio corrigido herda widget novo sem querer →
  Mitigação: slice 002 cobre os dois fluxos explicitamente.
- [Risco] Reestilizar decisão sugere mudar comportamento →
  Mitigação: diff de comportamento zero (mesmos POSTs, mesmas
  validações); testes existentes intocados passam sem alteração.

## Migration Plan

Sem migrations (widget + templates + JS). Deploy: estáticos + 2
forms. Rollback: revert.
