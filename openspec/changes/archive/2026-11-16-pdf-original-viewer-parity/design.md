# Design

## Context

Ver `proposal.md` (Why) e os deltas em `specs/`. Estado atual observado:

- Serve de PDF por papel já existe com guards próprios: `intake:serve_document` (por pk do documento, guard criador), `doctor:case_pdf` e `scheduler:case_pdf` (por `position`, guards matriz-subtipo e `scheduled_by`+status). Nenhum tem `@xframe_options_sameorigin` — suficiente para navegação direta (`target=_blank`), **insuficiente para `<embed>`** (ver D4).
- HMD é multi-PDF por caso (`CaseDocument` ordenado por `position`, texto concatenado em `Case.extracted_text`); ats-web é 1 PDF por caso (`case.pdf_file`). O padrão de cards precisa de um sub-collapse por documento.
- `CaseAttachment` usa PK int (sem uuid); não há rota de serve de anexos em nenhum papel.
- `XFrameOptionsMiddleware` ativo (`config/settings/base.py:168`) com `DENY` (default Django; explícito em `prod.py:147`).
- `templates/doctor/_case_cards.html` é partial compartilhado entre `decide.html` e `case_detail.html` — um ponto de edição cobre as duas páginas médicas.
- Stack: Django SSR, Bootstrap 5.3 via CDN, JS vanilla, `uv`+`ruff`+`mypy strict`+`pytest`.

**Nota — cardinalidade real documentos-por-caso (apurado 2026-11-15):** o
lote do NIR aceita 1–N PDFs com tipo único (`IntakeUploadForm`: `documents`
`multiple` + `procedure_type` ChoiceField único) e `submit_report_batch`
transforma cada PDF válido em um caso próprio com exatamente 1 `CaseDocument`
(`position=1`); o reenvio do gate também grava exatamente 1. O suporte a N
documentos por caso (`position`, concatenação ordenada, loops nos templates,
cenário scheduling "dois documentos") é legado do change 04 (quando 1 caso
= N PDFs), preservado na migração `intake-batch-semantics` — nenhum caminho
de escrita atual produz N>1 (só 2 fixtures de scheduler criam `position=2`
manualmente). Anexos (`CaseAttachment`) nunca entram em `case.documents`.
Na prática os cards renderizam 1 sub-collapse; o loop genérico permanece por
compatibilidade com o modelo/specs.

## Goals / Non-Goals

**Goals:**

- Paridade visual/comportamental com o ats-web nos 4 papéis, adaptada ao multi-PDF do HMD.
- Zero fetch de PDF no load da página de detalhe (lazy por expansão); em mobile o embed permanece oculto e não recebe `src` nem inicia carregamento; um `src` previamente atribuído em desktop pode ser mantido após resize.
- Reutilizar guards existentes sem enfraquecê-los; papel errado → 403, alcance/documento → 404 fail-closed.

**Non-Goals:**

- Mudar extração, FSM, guards ou políticas de PHI existentes.
- Supressão de anexo pelo NIR, viewer de imagem dedicado e histórico de casos encerrados do ats-web (fora desta change).
- Anexos no papel agendador (ambos os sistemas não têm anexos nesse escopo).
- Normalizar a cardinalidade documentos-por-caso (débito conhecido, ver Nota
  no Context): o suporte legado a N `CaseDocument` por caso (`position`,
  concatenação, loops) permanece intocado — follow-up sugerido
  `single-document-invariant` (endurecer o invariante "sempre 1" ou
  simplificar o modelo com migration + revisão das specs).

## Decisions

### D1 — Views montam `pdf_items`; template itera (sem lógica de URL no template)

Cada view de detalhe constrói `pdf_items: [{position, filename, size, serve_url, viewer_url}]` com `reverse()` das rotas do próprio papel e passa ao template, que itera gerando um sub-collapse por documento (`id="pdf-collapse-{{ position }}"`). Alternativa (partial único com `{% url %}` dinâmico) foi descartada: intake serve por pk e os demais por `position` — pré-montar na view isola a diferença e segue a regra "lógica nunca em templates".

### D2 — Lazy-embed via `data-src` + `show.bs.collapse` (novo `static/js/pdf-lazy-embed.js`)

Embed nasce sem `src` (só `data-src`); listener vanilla no evento Bootstrap `show.bs.collapse` **do sub-collapse de cada documento** (alvo específico — expandir o card externo não atribui `src` a nenhum embed; cada documento carrega independentemente) define `src` uma única vez (flag por elemento) e **somente com o breakpoint desktop ativo** — `matchMedia('(min-width: 768px)')`, cheque independente da visibilidade do ancestral (o evento dispara com o pai ainda fechado; teste literal de visibilidade do embed é proibido aqui). Mobile jamais inicia busca (sem atribuição de `src`); `src` já atribuído pode ser retido no resize desktop→mobile (keep-after-first-load). Transição de viewport: listener de mudança do breakpoint atribui `src` aos sub-collapses já abertos (`.show`) ainda sem `src` assim que viram desktop. Keep-after-first-load: não limpa `src` no `hide` (re-expansão instantânea; PDFs de relatório são pequenos). Fallback sem-JS: bloco `<noscript>` com links diretos (mesmos `serve_url`s de D1), fora dos collapses. Alternativas descartadas: `loading="lazy"` em iframe (carrega por proximidade do viewport, não por expansão — impreciso com collapse) e fetch+blob (quebra streaming do plugin e complica auth).

### D3 — Viewer mobile = port do ats-web (`mobile_pdf_viewer.html` + `pdf-viewer.js` + `vendor/pdfjs/`)

Mesma estrutura: `extends base.html`, nav sticky com `← Voltar` topo/rodapé, `data-pdf-url`/`data-fallback-pdf-url`, import dinâmico do módulo viewer com PDF.js vendored. Rotas de viewer por documento: `doctor/scheduler: case/<uuid>/pdf/<position>/viewer/`, `intake: cases/<uuid>/documents/<pk>/viewer/`, `dashboard: case/<uuid>/pdf/<position>/viewer/`. Anexos: viewer só para PDF (`.../attachments/<int>/viewer/`); imagens usam link direto + `<img loading="lazy">` (sem viewer dedicado — non-goal).

### D4 — `@xframe_options_sameorigin` em TODAS as rotas de serve (existentes + novas)

O middleware de clickjacking nega framing por padrão; sem o decorator o `<embed>` same-origin é bloqueado (ats-web decora todos os serves — `apps/*/views.py`). Aplicar o decorator às 3 rotas existentes (`intake:serve_document`, `doctor:case_pdf`, `scheduler:case_pdf`) e às novas (dashboard serve + serves de anexo). Risco avaliado: SAMEORIGIN só permite framing pelo próprio host — sem abertura a terceiros. Teste: assert no header por rota.

### D5 — Helper `resolve_safe_next_url` em `apps/cases/navigation.py` (novo, espelho ats-web)

Mesma semântica do original (`url_has_allowed_host_and_scheme`, `allowed_hosts={host}`, `require_https=is_secure()`, fallback ao detalhe). Testado uma vez; as 4+ views de viewer reutilizam. Alternativa (helper por app) descartada: duplicação de lógica de segurança.

### D6 — Serve de anexos por papel, guard = guard do detalhe do papel

Novas rotas `intake:serve_attachment cases/<uuid>/attachments/<int>/`, `doctor:serve_attachment case/<uuid>/attachments/<int>/`, `dashboard:serve_attachment case/<uuid>/attachments/<int>/` (pk int, padrão do `serve_document`; 404 fail-closed mesma semântica do serve de documentos de cada papel). Content-Type original do upload (`application/pdf`/`image/*`), `FileResponse`, `@xframe_options_sameorigin`. Sem rota no scheduler (non-goal).

### D7 — Card de texto do agendador sob o MESMO gate do PDF (`can_view_pdf`)

Texto e PDFs aparecem juntos só após a decisão de agendamento em casos processados pelo próprio agendador — preserva a intenção "limitada ao necessário" da spec com a extensão decidida pelo dono. Implementação: o `{% if can_view_pdf %}` existente passa a envolver os dois cards.

### D8 — Posição dos cards por papel (ordem de leitura preservada)

- NIR: substituem a tabela "Documentos" no mesmo ponto; anexos ganham visualização na seção existente.
- Médico: dentro de `_case_cards.html`, no ponto atual do card "Documentos" (após Prior-case, antes de Requisitos — ordem clínica intacta); card de anexo ganha bloco de arquivo após evidência.
- Agendador: no ponto atual do bloco `can_view_pdf`.
- Painel: após "Procedimentos declarados", antes da "Trilha de eventos"; anexos após os cards de documento.

## Risks / Trade-offs

- [Embed bloqueado por header] → Mitigação: D4 + teste de header por rota; fallback "Abrir em nova aba" sempre presente.
- [PDF.js vendored defasado] → Mitigação: copiar a versão exata do ats-web (comportamento conhecido); sem upgrade nesta change.
- [N sub-collapses com N documentos grandes] → Mitigação: lazy garante no máximo os expandidos em memória; keep-after-first-load é aceitável para relatórios (mesmo perfil do ats-web).
- [`extracted_text` vazio no agendador/painel] → Mitigação: card omitido quando vazio (spec); sem placeholder fora do médico.
- [Caso `CLEANED` minimiza `extracted_text`/`structured_data`] → Sem impacto: cards condicionais somem naturalmente; serve de documento segue o guard de cada papel (documentos não são minimizados no fechamento — comportamento existente preservado).

## Migration Plan

Deploy sem migração (só código + templates + static). Rollback = revert do deploy; nenhuma persistência nova. Ordem de slices entrega valor incremental por papel (ver `tasks.md`).

## Open Questions

Nenhuma — as 3 decisões de produto foram resolvidas com o dono antes da proposta (agendador com texto: sim; lazy por documento: sim; painel com PDF/texto irrestrito a manager/admin: sim).
