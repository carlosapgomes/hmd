# Tasks: ui-parity-intake-detail

## 1. Upload com drag-and-drop

Contract: `slices/slice-001-upload-dragdrop.md`

- [x] 1.1 `upload-zone` com drag-and-drop + preview (lista,
  contagem, tamanho total) sobre os fields HMD, mantendo lote
  homogêneo, limites e seção de anexos; RED: testes de marcadores
  e comportamento falham no HTML/JS atual; verificar com `pytest` +
  jornada do harness (lote válido cria casos; inválido preserva erro)
- [x] 1.2 Reenvio corrigido com o mesmo widget (1 PDF); verificar
  com testes do fluxo + jornada (sem JS o form nativo submete)

## 2. Combobox de procedimento

Contract: `slices/slice-002-procedure-combobox.md`

- [x] 2.1 `procedure_type` como select canônico + combobox
  pesquisável (aliases do catálogo, teclado, ARIA); validação e
  catálogo intactos; verificar com testes do form + jornada
  (com e sem JS)
- [x] 2.2 Aplicar ao envio e ao reenvio corrigido; verificar com
  testes dos dois fluxos + capturas vs ats-web

## 3. Detalhe, decisão e confirmação no visual

Contract: `slices/slice-003-detail-decision-visual.md`

- [ ] 3.1 Detalhe do caso (médico, agendador, NIR) no layout
  ats-web com o conteúdo atual intacto; verificar com testes de
  conteúdo + capturas vs ats-web
- [ ] 3.2 Decisão e confirmação/negação no layout, formulários e
  atomics intactos (zero comportamento portado); verificar com a
  suíte existente verde sem alteração + jornada de decisão completa

## 4. Painel no visual

Contract: `slices/slice-004-panel-visual.md`

- [ ] 4.1 Toolbar de período, cards de métrica e lista em
  `case-card` com queries e filtros atuais; verificar com testes
  de conteúdo + capturas vs ats-web
- [ ] 4.2 Rodar o mapa verify-hmd completo, gate verde e
  `PROJECT_CONTEXT.md` atualizado para o archive; verificar com
  evidência do harness + gate
