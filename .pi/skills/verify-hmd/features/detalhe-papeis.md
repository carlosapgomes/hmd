# Detalhe, decisão e confirmação por papel (slice 003)

Prova da jornada completa médico → agendador → NIR no layout ats-web
(ui-parity-intake-detail, slice 003, R1–R3): detalhe do caso (médico,
agendador, NIR), página de trabalho/decisão e confirmação do agendador
com o mesmo conteúdo e comportamento do layout antigo — só classes e
estrutura visual mudam (ordem clínica contratada, decisão atômica,
reservas/heartbeat e validações intactos). Sobre a instância isolada
do `verify-hmd`, com o caso sintético `VERIFY-001`.

## Jornadas

- `doctor-detalhe-decide`: `verify-doctor` logado pelo form → fila
  médica → página de trabalho do `VERIFY-001` com
  `.doctor-decision-layout`, cards `.doctor-report-card`,
  `.doctor-report-grid`, `.report-block-body` e cartão
  `.doctor-decision-form-card`; ordem clínica pinada no DOM
  (Procedimentos → Sumário → Estrutura → Prior-case → Documentos →
  Requisitos → Alertas); reserva com `lock_token` não-vazio;
  aprova todos os procedimentos e *Registrar decisão* → detalhe
  read-only com *Decisões registradas* e *Aprovado*.
- `scheduler-detalhe-confirm`: `verify-scheduler` logado pelo form →
  fila de agendamento → detalhe com `.detail-layout`, cards
  `.report-card`, `.summary-box` e cartões
  `.scheduler-decision-form-card` (confirmar/negar), `lock_token` e
  heartbeat intactos → confirma unidade 1 com data/hora futuras e
  local → detalhe exibe o agendamento.
- `nir-detalhe-before-ack`/`nir-detalhe-cleaned`: `verify-nir` logado
  pelo form → detalhe com `.detail-layout`/`.report-card`/
  `.report-block-body`, seção *Resultado* com as decisões →
  *Confirmar recebimento* → caso concluído (*Caso concluído* /
  *Reenviar corrigido*).

## Como dirigir (Playwright)

```bash
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
node .pi/skills/verify-hmd/scripts/browser/drive-detalhe-papeis.mjs --run <tmp/verification/hmd-verify-...>
```

O drive recusa rodar sem posse da instância (doctor prévio via
`control-hmd doctor`). Capturas: `doctor-detalhe-decide`,
`doctor-detalhe-decide-filled`, `doctor-detalhe-decided`,
`scheduler-detalhe-confirm`, `scheduler-detalhe-confirm-filled`,
`scheduler-detalhe-confirmed`, `nir-detalhe-before-ack`,
`nir-detalhe-cleaned` (+ `report.json`/`observed-state.json`).

## Gotchas

- A página de trabalho do médico reserva o caso (`doctor_decision`) e
  o detalhe do agendador reserva (`scheduler_confirm`) — é o
  comportamento normal de leitura, não prova de decisão. Cleanup
  destrói o banco inteiro, sem vazamento entre runs.
- O caso sintético é setup, nunca prova clínica. `VERIFY-002` fica
  intacto para as demais jornadas da mesma run.
- Ordem condicional populada (`Anexos` + `Recomendação agregada do
  pipeline` entre `Documentos`→`Requisitos`→`Alertas`): provada no pytest
  (`test_doctor_detail_orders_conditional_cards_when_populated`). O drive
  segue conferindo só os cards sempre-presentes: o seed sintético não
  renderiza os condicionais e não há fluxo UI aprovado para popular
  `VERIFY-001` (anexos só no envio de intake; agregado vem do LLM2), e o
  drive veda mutação direta de banco — popular no seed arriscaria as
  jornadas 001/002/004.

## PVP-002 — viewer + cards do relatório NIR (VERIFY-101)

Prova dos cards `Texto Extraído do PDF` e `Visualizar PDF Original` (lazy no
desktop, viewer dedicado no mobile) no detalhe do NIR
(pdf-original-viewer-parity, slice PVP-002), sobre a instância isolada do
`verify-hmd` com o caso sintético `VERIFY-101`.

- Fixture criada NO DRIVE via runVerifyPy (nunca no seed compartilhado):
  criador `verify-nir`, `AWAITING_DOCTOR`, 2 `CaseDocuments` com PDFs
  VÁLIDOS (PyMuPDF, 1 página com texto "VERIFY-101 doc N") gravados no
  storage da run + `extracted_text` populado; IDs/nomes fora de
  `VERIFY-001`/`VERIFY-002` e das faixas de outros drives.
- Rede (contagem fail-loud no `server.log` entre marcos de fase, pelo caminho
  REAL `GET /intake/cases/.../documents/...`; verificações diretas usam
  `?probe=1` e são excluídas): detalhe fechado = 0 GET; card externo
  aberto = 0 GET; expansão do sub-collapse do doc 1 ⇒ ≥1 GET sem marcador
  SÓ em `documents/1/` (doc 2 sem GET e sem `src`); mobile 390 = 0 GET de
  embed; resize 390→1280 com sub-collapse aberto ⇒ ≥1 GET sem marcador em
  `documents/2/`; passada sem JS prova o `<noscript>` visível e funcional
  (link direto serve o PDF 200 `application/pdf`).
- EVIDÊNCIA FORTE (modo headed sob Xvfb — o único em que o fetch do plugin é
  observável): no Chromium pinado (1.64.0/chromium-1248) o plugin de PDF
  BUSCA o `<embed>` cujo `src` foi atribuído via JS — o `server.log`
  registra GET sem marcador após cada expansão (no modo headless o plugin
  não busca nem compõe o embed — experimentos do fix-1). A prova de
  RENDERIZAÇÃO segue a decisão C do slice (viewer PDF.js mobile: `#pdf-pages
  canvas` não-branco, mesma rota de serve). Sem overclaim: o drive prova o
  fetch do embed (GET sem marcador target-only) + mecanismo D2 (`src` só no
  alvo, uma única vez) + 0 requisições quando fechado/mobile + renderização
  via PDF.js canvas.
- RETIFICAÇÃO DO FIX-1 (2026-11-15): o relatório fix-1 afirmava "nenhum GET
  sem marcador" (só GETs com `?probe=1`) — FALSO. O `server.log` da run
  final do fix-1 já continha os GETs sem marcador pós-expansão; o contador
  do drive é que estava quebrado (off-by-one no marco + filtro
  `GET /documents/` que nunca casava o caminho real
  `GET /intake/cases/.../documents/...`). Corrigido no fix-2 (P1-a).
- Viewer mobile do doc 1 (PDF.js renderiza o PDF em canvas) com `← Voltar`
  no topo (sticky) e no rodapé; `?next=` inseguro cai no detalhe do caso.
- R5: subprocesso e servidor usam o mesmo `HMD_VERIFY_MEDIA_ROOT` validado
  sob a run (manifest `mediaRoot`, guarda de caminho real `_media_path_ok_`/
  `isMediaUnderRun` contra traversal/symlink); os PDFs da fixture estão no
  root da run e são servidos; o cleanup remove só a mídia da run (com
  guarda).
- Evidência: `nir-101-detalhe-1280-fechado`, `nir-101-card-externo-aberto`,
  `nir-101-doc1-expandido`, `nir-101-detalhe-390`, `nir-101-viewer-mobile`,
  `nir-101-resize-1280-carregado`, `nir-101-sem-js-noscript` (+ `report.json`/
  `observed-state.json`).

```bash
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
node .pi/skills/verify-hmd/scripts/browser/drive-detalhe-papeis.mjs --run <tmp/verification/hmd-verify-...>
```
