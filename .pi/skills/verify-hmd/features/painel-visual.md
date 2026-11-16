# Painel gerencial no visual (ui-parity-intake-detail slice 004)

Prova do painel no padrão ats-web (ui-parity-intake-detail, slice 004,
R1–R3): toolbar de período, cards de métrica e lista em `case-card`
com os mesmos números e filtros de antes — só classes e estrutura
visual mudam (queries, filtros e fontes imutáveis intactos — paridade
funcional de métricas é change futuro). Sobre a instância isolada do
`verify-hmd`, com os casos sintéticos `VERIFY-001`/`VERIFY-002`.

## Jornadas

- `manager-painel`: `verify-manager` logado pelo form → `/dashboard/`
  com `.metrics-period-card` + `#metrics-period-selector` (4 opções
  Hoje/7 dias/30 dias/Tudo, ativa no default `hoje`), 6
  `.metrics-summary-card` (Total/Agendados/Negados/Em andamento/
  Encerrados/Encerr. administrativos), tabela *Procedimentos por
  tipo*, *Agendamentos por unidade*, *Tempo médio* e 2 `.case-card`
  (`VERIFY-001`/`VERIFY-002` com pacientes sintéticos e link de
  detalhes).
- `manager-painel-7d`: clique em *7 dias* na toolbar → URL
  `?period=7d` com `is-active` no *7 dias*; os mesmos 2 cards.
- `manager-painel-filtered`: busca `VERIFY-001` no campo *Busca* +
  *Filtrar* → 1 card (server-side, AND com os demais filtros);
  `VERIFY-002` some. Navegação nova para `/dashboard/` restaura os 2.
- `admin-painel`: `verify-admin` logado pelo form → mesma superfície
  do painel (mesma rota por papel ativo manager/admin).

## Como dirigir (Playwright)

```bash
TEST_DB_PORT=55435 .pi/skills/verify-hmd/scripts/control-hmd launch
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
node .pi/skills/verify-hmd/scripts/browser/drive-painel-visual.mjs --run <tmp/verification/hmd-verify-...>
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <tmp/verification/hmd-verify-...>
```

O drive recusa rodar sem posse da instância (doctor prévio via
`control-hmd doctor`). Capturas: `manager-painel`,
`manager-painel-7d`, `manager-painel-filtered`, `admin-painel`
(+ `report.json`/`observed-state.json`).

## PVP-005 — viewer + dois cards do painel sobre VERIFY-101

Detalhe do painel (`dashboard:case_detail`) com os cards do relatório nas
rotas próprias do painel (serve por `position` + viewer mobile, guard
`manager`/`admin`):

- Fixture criada NO DRIVE via runVerifyPy (nunca no seed compartilhado):
  `VERIFY-101` com criador `verify-nir`, `AWAITING_DOCTOR`, 2 PDFs VÁLIDOS
  (PyMuPDF, 1 página com texto `VERIFY-101 doc N`) gravados no storage da
  run + `extracted_text` populado; IDs/nomes fora de `VERIFY-001`/`VERIFY-002`.
- ORDEM OBRIGATÓRIA: todos os passos/asserts das jornadas acima (exigindo
  exatamente os 2 casos do seed, incl. manager/admin e reset) rodam ANTES;
  SÓ DEPOIS o drive cria VERIFY-101 e verifica seu detalhe. Nenhum assert
  de contagem de 2 casos é repetido após a criação.
- Detector: `dashboard/case/<id>/` com `Texto Extraído do PDF` em `<pre>`
  (omitido quando vazio) + `Visualizar PDF Original` (sub-collapse por doc
  com `data-pdf-doc-collapse`, embed lazy `data-src` sem `src`, `Abrir em
  nova aba`, link mobile `d-md-none` identificando `PDF N — arquivo` com
  `?next=`, `<noscript>` fora dos collapses, `pdf-lazy-embed.js`).
- Lazy por URL EXATA de serve desde ANTES do goto como GATE (ambos os
  docs, incluindo a navegação): fechado e card-aberto = 0 fetch; expansão
  ⇒ ≥1 GET sem marcador SÓ no alvo; doc 2 sem `src`; resize 390→1280
  (setViewportSize no mesmo detalhe, doc 2 aberto) carrega o alvo;
  mobile 390 = 0 fetch + 0 src (mesmo marco pré-goto até o fim da seção);
  viewer mobile (PDF.js, Voltar topo/rodapé, `?next=` inseguro → fallback).
- R5: subprocesso e servidor usam o mesmo `HMD_VERIFY_MEDIA_ROOT` validado
  (descendente estrito da run); PDFs sob o root da run; Chromium headed sob
  Xvfb (o headless não busca `<embed>`).
- Bundle PVP-007 (higiene da review-2 do PVP-005, arquivo já no escopo): o
  contador de rede passa a contar GETs de QUALQUER status na URL exata
  (não só ` 200`), mantendo a exclusão de probes — corrige o P2
  recorrente "gates ignoram 403/404" neste drive.

## PVP-007 — seção de anexos do painel sobre VERIFY-103 (R1–R2)

Seção "Anexos" no detalhe do painel (`dashboard:case_detail`) nas rotas
próprias do painel (serve `attachments/<int>/` por pk + viewer mobile SÓ
para PDF, guard `manager`/`admin`):

- Fixture criada NO DRIVE via runVerifyPy (nunca no seed compartilhado),
  SÓ DEPOIS de todos os asserts existentes de exatamente 2 casos (ordem
  §8: VERIFY-101 e os passos das jornadas acima rodam ANTES):
  `VERIFY-103` com criador `verify-nir`, `AWAITING_DOCTOR`, 2 PDFs VÁLIDOS
  de relatório (PyMuPDF, 1 página com texto `VERIFY-103 doc N`) + 1 anexo
  PDF válido (texto `VERIFY-103 anexo PDF`) + 1 anexo JPEG real (pixmap
  PyMuPDF do texto `VERIFY-103 anexo JPEG`), rows diretas com paths
  seguros por UUID (mesmo callable), IDs/nomes fora das demais faixas.
- Detector: `dashboard/case/<id>/` com a seção `Anexos` APÓS os cards de
  documento e ANTES da trilha (D8): cada anexo com nome + status legível +
  `Abrir anexo` (serve do painel, `target=_blank`) e visualização
  colapsável — PDF recebe collapse com `data-pdf-doc-collapse` + embed
  lazy `data-src` sem `src` + link mobile `d-md-none` identificando
  `Anexo N — nome` ao viewer dedicado com `?next=`; imagem recebe collapse
  com `<img loading="lazy" src=serve>` + link direto (sem viewer);
  `<noscript>` com links diretos por anexo; `pdf-lazy-embed.js` mantido.
- Lazy por URL EXATA de serve desde ANTES do goto como GATE (ambos os
  anexos, incluindo a navegação; qualquer status conta — bundle PVP-007):
  fechado = 0 fetch; expansão do anexo PDF ⇒ ≥1 GET sem marcador SÓ no
  alvo; anexo JPEG sem `src`/sem viewer; mobile 390 = 0 fetch + 0 src
  (mesmo marco pré-goto até o fim da seção); viewer mobile do anexo
  (PDF.js, Voltar topo/rodapé, `?next=` inseguro → fallback); verificação
  direta do serve JPEG (`?probe=1`, fora da contagem).

## Gotchas

- Os casos sintéticos `VERIFY-001`/`VERIFY-002` (`AWAITING_DOCTOR`)
  são setup, nunca prova de métrica: os números exatos do período
  ficam provados pelo pytest (`test_panel_visual.py` — valores
  renderizados = `compute_summary`), e a suíte de métricas existente
  passa sem alteração (prova R2).
- A toolbar preserva os filtros da lista ao trocar o período
  (`list_qs`) — o caminho de filtro é provado após o clique em
  *7 dias*, com busca + período vigentes juntos.
- Cleanup destrói o banco inteiro, sem vazamento entre runs.
