# Proposal: ui-parity-intake-detail

## Why

Falta a última milha da paridade: o envio do NIR ainda é inputs nativos
sem drag-and-drop nem preview; o tipo de procedimento do lote é uma
lista de 13 radios (no ats-web, um select pesquisável); e detalhe do
caso, decisão, confirmação e painel usam o layout antigo — mesmo com a
casca, as fontes e o header já alinhados. Este change fecha a paridade
visual e de interação em todas as superfícies restantes, sem tocar em
regra de negócio, validação, FSM ou métricas.

## What Changes

1. **Upload NIR com drag-and-drop**: `upload-zone` com preview
   (arquivos, contagem, tamanho total), mantendo a semântica HMD de
   lote homogêneo (1 PDF = 1 caso, um `procedure_type` por lote),
   limites, validação server-side e a seção de anexos do change 10.
2. **Combobox de procedimento**: `procedure_type` deixa de ser radio
   list e vira select + combobox pesquisável (busca em labels +
   códigos dos 13 perfis, ignora acentos, navegação por teclado), com progressive
   enhancement (sem JS, o select canônico funciona); validação e
   catálogo inalterados.
3. **Detalhe/decisão/confirmação no visual**: detalhe do caso
   (médico, agendador, NIR), página de trabalho/decisão e páginas de
   confirmação/negação do agendador ganham o layout do ats-web
   (tabelas de identificação, blocos de relatório, badges) —
   comportamento e formulários intactos (decisão por procedimento
   atômica inalterada).
4. **Painel no visual**: seletor de período, cards de métrica e lista
   de casos no padrão ats-web, com as métricas e filtros atuais do
   HMD intactos.

## Capabilities

### New Capabilities

(nenhuma — paridade de apresentação e interação sobre comportamentos
já especificados.)

### Modified Capabilities

- `intake-nir`: reconciliação da criação com o comportamento vigente
  (débito pré-existente de spec, não regressão deste change): 1 PDF =
  1 caso (transação independente), lote homogêneo com tipo único,
  arquivo inválido vira erro nomeado sem bloquear os demais, anexos
  só com 1 relatório (erro não-bloqueante com multi-PDF, aborta com
  1 PDF). Cenários antigos preservados com conteúdo atualizado.
- `attachments`: idem para o upload de anexos (anexos exigem 1
  relatório; multi-PDF gera erro não-bloqueante).

## Impact

- **Código**: `templates/intake/home.html` (+ `corrected_resubmission`
  se compartilha o widget), `static/js/intake-upload.js` (rework
  sobre o molde `upload.js` do ats-web com field names e limites
  HMD), `apps/intake/forms.py` (`procedure_type` vira select +
  combobox) + `static/js/procedure_combobox.js` (molde adaptado,
  busca em labels + códigos canônicos), `templates/doctor/
  decide.html` + `case_detail.html`, `templates/scheduler/
  case_detail.html` (forms embutidos), `templates/dashboard/*`,
  `.pi/skills/verify-hmd/` (4 jornadas).
- **Specs**: `intake-nir`, `attachments` (MODIFIED — reconciliação
  com o comportamento vigente; sem mudança de código de negócio).
- **Comportamento preservado**: limites de upload, validação,
  semântica de lote homogêneo, catálogo de 13 perfis, decisão
  atômica por procedimento, reservas, métricas do painel.
- **Fora de escopo**: regra de negócio nova, paridade funcional de
  métricas, rename do ats-web.
