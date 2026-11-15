# Tasks: pdf-original-viewer-parity

## 1. Slice 001 — Infra compartilhada do viewer

Contract: `slices/slice-001-shared-viewer-infra.md`

- [x] 1.1 Escrever RED de `resolve_safe_next_url`, template mobile, estáticos (guardas em disco) e header SAMEORIGIN nas 3 rotas de serve; executar os testes focados e registrar as falhas esperadas.
- [x] 1.2 Implementar helper, template, vendor PDF.js + viewers JS e o decorator nas rotas existentes; verificar o conjunto focado verde e as suítes de serve/guards sem regressão.
- [x] 1.3 Verificar `collectstatic --dry-run` e o contrato `data-src` do lazy-embed (prova comportamental no browser fica no runtime de PVP-002, primeira página consumidora).
- [x] 1.4 Rodar o quality gate, produzir `/tmp/hmd-slice-pvp-001-report.md` e submeter o slice à revisão independente antes de avançar.

## 2. Slice 002 — NIR: viewer + dois cards

Contract: `slices/slice-002-nir-report-cards.md`

- [x] 2.1 Escrever RED da rota `intake:pdf_viewer` (guards/next/404), dos cards no detalhe e do isolamento de mídia da run; executar os testes focados e registrar as falhas esperadas.
- [x] 2.2 Implementar viewer, `pdf_items`, os dois cards (lazy + mobile link), a fixture VERIFY-101 via runVerifyPy e o MEDIA_ROOT por run; verificar testes focados verdes e a suíte intake sem regressão.
- [x] 2.3 Rodar `detalhe-papeis` sobre VERIFY-101 (runVerifyPy com media root do manifest validado): provar lazy isolado, resize, viewer e acesso sem JS; comprovar arquivo servido do root da run e cleanup seguro, com PNG/snapshot/JSON.
- [x] 2.4 Rodar o quality gate, produzir `/tmp/hmd-slice-pvp-002-report.md` e submeter o slice à revisão independente antes de avançar.

## 3. Slice 003 — Médico: viewer + dois cards no partial

Contract: `slices/slice-003-doctor-report-cards.md`

- [x] 3.1 Escrever RED da rota `doctor:pdf_viewer` (matriz/next/404) e dos cards nas duas páginas; executar os testes focados e registrar as falhas esperadas.
- [x] 3.2 Implementar viewer e os dois cards no partial `_case_cards.html` (placeholder sem texto); verificar testes focados verdes e a suíte doctor sem regressão.
- [x] 3.3 Rodar `detalhe-papeis` (médico, detalhe + decisão) sobre VERIFY-101 em 1280/390, com PNG/snapshot/JSON.
- [x] 3.4 Rodar o quality gate, produzir `/tmp/hmd-slice-pvp-003-report.md` e submeter o slice à revisão independente antes de avançar.

## 4. Slice 004 — Agendador: viewer + dois cards sob o gate

Contract: `slices/slice-004-scheduler-report-cards.md`

- [x] 4.1 Escrever RED da rota `scheduler:pdf_viewer` (gate fail-closed) e dos cards dentro/fora do gate; executar os testes focados e registrar as falhas esperadas.
- [x] 4.2 Implementar viewer e os dois cards sob `can_view_pdf`; verificar testes focados verdes e a suíte scheduler sem regressão.
- [x] 4.3 Estender/rodar a jornada `detalhe-papeis` (agendador, dentro/fora do gate) em 1280/390, com PNG/snapshot/JSON.
- [x] 4.4 Rodar o quality gate, produzir `/tmp/hmd-slice-pvp-004-report.md` e submeter o slice à revisão independente antes de avançar.

## 5. Slice 005 — Painel: serve + viewer + dois cards

Contract: `slices/slice-005-dashboard-report-cards.md`

- [ ] 5.1 Escrever RED das rotas `dashboard:case_pdf` + `dashboard:pdf_viewer` (403/404/headers) e dos cards; executar os testes focados e registrar as falhas esperadas.
- [ ] 5.2 Implementar serve, viewer, `pdf_items` e os dois cards (posição D8); verificar testes focados verdes e a suíte dashboard sem regressão.
- [ ] 5.3 Rodar `painel-visual` em 1280/390: concluir os asserts existentes de dois casos antes de criar a fixture, depois verificar o detalhe novo; registrar PNG/snapshot/JSON.
- [ ] 5.4 Rodar o quality gate, produzir `/tmp/hmd-slice-pvp-005-report.md` e submeter o slice à revisão independente antes de avançar.

## 6. Slice 006 — Anexos NIR + médico com visualização

Contract: `slices/slice-006-attachment-viewing-nir-doctor.md`

- [ ] 6.1 Escrever RED dos serves + viewers de anexo NIR/médico e da visualização nos cards (PDF/JPEG); executar os testes focados e registrar as falhas esperadas.
- [ ] 6.2 Implementar rotas e blocos de arquivo nos cards (embed lazy PDF, imagem lazy, viewer mobile só PDF); verificar testes focados verdes e as suítes attachments/intake/doctor sem regressão.
- [ ] 6.3 Estender/rodar a jornada `detalhe-papeis` (anexos NIR + médico, PDF/JPEG; setup VERIFY-103 via runVerifyPy) em 1280/390, com PNG/snapshot/JSON.
- [ ] 6.4 Rodar o quality gate, produzir `/tmp/hmd-slice-pvp-006-report.md` e submeter o slice à revisão independente antes de avançar.

## 7. Slice 007 — Anexos no painel + verificação final

Contract: `slices/slice-007-attachment-viewing-dashboard.md`

- [ ] 7.1 Escrever RED do serve + viewer de anexo do painel e da seção de anexos; executar os testes focados e registrar as falhas esperadas.
- [ ] 7.2 Implementar rotas e seção (omitida sem anexos); verificar testes focados verdes e a suíte dashboard sem regressão.
- [ ] 7.3 Rodar `detalhe-papeis` + `painel-visual` na árvore final (4 papéis, 1280/390); no painel concluir os asserts de dois casos antes das fixtures, usando media root validado, e registrar PNG/snapshot/JSON; mapear regressões.
- [ ] 7.4 Rodar o quality gate final, produzir `/tmp/hmd-slice-pvp-007-report.md` e submeter o slice à revisão independente (change pronta para archive).
