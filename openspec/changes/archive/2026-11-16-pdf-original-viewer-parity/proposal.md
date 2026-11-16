# Proposal

## Why

Os detalhes de caso do HMD dão acesso ao PDF original por links soltos ("Abrir PDF" em tabelas), divergindo do sistema de referência (ats-web), onde cada papel vê dois cards colapsáveis padronizados — `Texto Extraído do PDF` e `Visualizar PDF Original` (embed no desktop, viewer dedicado com PDF.js no mobile). O supervisor (painel) hoje não tem acesso algum ao PDF, e os anexos de evidência não têm visualização do arquivo em nenhum papel. Esta change traz a paridade de visualização para os 4 papéis.

## What Changes

- Dois cards colapsáveis padronizados nos detalhes de caso dos 4 papéis (NIR, médico, agendador, supervisor/painel):
  - `Texto Extraído do PDF` — `<pre>` com `Case.extracted_text` (fonte única já existente).
  - `Visualizar PDF Original` — desktop: `<embed>` colapsável por documento com carregamento lazy (só busca o PDF na primeira expansão) + botão "Abrir em nova aba"; mobile: link para viewer dedicado.
- Viewer mobile dedicado por papel (`pdf-viewer/mobile_pdf_viewer.html`, padrão ats-web): nav sticky com botão `← Voltar` (topo e rodapé), renderização via PDF.js vendored com loading e fallback para o PDF direto; `?next=` resolvido de forma segura (helper `resolve_safe_next_url`).
- Rotas de serve/viewer por papel com os mesmos guards já vigentes (NIR: criador; médico: matriz de subtipo; agendador: `scheduled_by` + status pós-decisão; painel: manager/admin — **rotas novas**, pois o painel hoje não serve PDF).
- Card de anexos com visualização do arquivo: rotas de serve de anexos por papel (hoje inexistentes) + embed lazy para anexos PDF / imagem lazy para JPEG-PNG, separados do card do relatório original. Últimos slices da change.
- Adaptação multi-PDF: o HMD tem 1–N `CaseDocument` por caso (ats-web tem 1); cada documento ganha seu sub-collapse lazy dentro do card `Visualizar PDF Original`.

## Capabilities

### New Capabilities

- `pdf-original-viewer`: visualização do relatório original (texto extraído + PDFs) e dos arquivos de anexo nos detalhes de caso dos 4 papéis, com cards colapsáveis, embed lazy no desktop e viewer mobile com PDF.js.

### Modified Capabilities

- `scheduling`: o detalhe do agendador passa a exibir o card `Texto Extraído do PDF` (decisão do dono; estende a lista fechada "apenas …" do requisito `Visão do agendador limitada ao necessário`). O gate temporal do PDF (só após a decisão de agendamento, só casos processados pelo próprio agendador) permanece inalterado.
- `dashboard`: o detalhe do caso no painel passa a exibir os cards de documentos/texto extraído (hoje o supervisor não tem acesso ao PDF).
- `attachments`: anexos de evidência passam a ter o arquivo servido e visualizável (embed/imagem) nos cards de anexo do NIR, médico e painel, além dos metadados/status atuais.

## Impact

- Templates: `intake/case_detail.html`, `doctor/_case_cards.html` (cobre `decide.html` + `case_detail.html`), `scheduler/case_detail.html`, `dashboard/case_detail.html`, novo `pdf_viewer/mobile_pdf_viewer.html`.
- Views/URLs: novos viewers `intake:pdf_viewer`, `doctor:pdf_viewer`, `scheduler:pdf_viewer`, `dashboard:case_pdf` + `dashboard:pdf_viewer`, serve de anexos por papel (`intake:serve_attachment`, `doctor:serve_attachment`, `dashboard:serve_attachment`; agendador: a definir no design — sem anexos hoje), novo helper `resolve_safe_next_url`.
- Static: novo `static/js/pdf-lazy-embed.js` (vanilla), port de `static/js/pdf-viewer.js` + `static/vendor/pdfjs/` do ats-web.
- Sem migrations, sem dependências novas (PDF.js é static vendored), sem mudança nos guards existentes — apenas reutilização.
