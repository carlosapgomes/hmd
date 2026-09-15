# Proposal: painel-ats-parity

## Why

O dono definiu o alvo do painel: **paridade com o dashboard do ats-web**.
Além disso, CORRIGIU a política de PHI: **zero-PHI é exigência do perímetro
externo (LLMs/serviços de terceiros), não da UI interna** — o sistema
opera dentro do hospital com funcionários que precisam da identificação
completa dos pacientes. A decisão anterior de lista zero-PHI do painel
(identification por nº de ocorrência) é REVERTIDA; as métricas seguem sem
dados de paciente por natureza (contagens).

Faltam hoje (comparação direta com o ats-web): unidade de origem (campo
nem existe — o relatório traz `Unid. Origem:` no cabeçalho, não
capturado), cards do painel com identificação completa, busca por data
(range), filtro por tipo de exame, default «hoje em todos os estados»,
detail do caso no painel (inexiste — só há rotas de encerramento) e a
trilha de eventos com rótulos legíveis (hoje só no NIR/médico, com termos
técnicos).

## What Changes

1. **Unidade de origem**: extração determinística do cabeçalho
   (`Unid. Origem:` rótulo sozinho + valor na linha seguinte — mesma forma
   multilinha de `Dias em tela`) + campo `Case.origin_unit` (CharField,
   migration) persistido pelo worker pdf e zerado no reenvio.
2. **Lista do painel (paridade ats-web)**: cards com **nome do paciente,
   idade, unidade de origem, exames declarados, fase do fluxo (próximo
   passo), data/hora de inserção e botão [Detalhes]**; filtros que compõem
   por AND: **busca por data (`date_from`/`date_to`)**, status, **tipo de
   exame (dropdown com os tipos do catálogo)**, busca textual (nome do
   paciente, nº de ocorrência ou prefixo do uid, ≥3 caracteres); **default
   «hoje, todos os estados»** (incluindo `CLEANED`); paginação preservando
   filtros; encerramento administrativo preservado (migra para o detail).
3. **Detalhe do caso no painel**: rota `dashboard:case_detail` com
   identificação completa, procedimentos, fase e **trilha de eventos com
   rótulos legíveis** (mapa `EVENT_LABELS`-style dos ~23 tipos de evento
   do HMD, molde ats-web `EVENT_LABELS` + dots), **collapsible fechado por
   padrão**; hospeda a ação de encerramento administrativo existente.
4. **Trilha sai do NIR e do médico**: os detalhes de NIR e médico não
   renderizam mais a trilha; caso `FAILED` exibe **badge de erro**
   (estilo ats-web).

## Impact

- **Specs**: `dashboard` (lista MODIFIED + detail ADDED), `intake-nir`
  (extração MODIFIED +unidade; detalhe NIR MODIFIED sem trilha),
  `doctor-decision` (detalhe decidido MODIFIED sem trilha; presenter
  MODIFIED +cenário sem trilha em decisão).
- **Código**: `apps/intake/pdf_utils.py`/`tasks.py`/`services.py`,
  `apps/cases/models.py` (+migration), `apps/dashboard/views.py`/
  `case_labels.py`(+`event_labels`)/templates, `templates/intake/
  case_detail.html`, `templates/doctor/case_detail.html`.
- **Política**: zero-PHI da LISTA do painel revertido por decisão do dono
  (UI interna = funcionários; perímetro externo segue tokenizado — guard
  do pipeline intocado). Métricas permanecem contagens (sem dados de
  paciente por natureza).
- **Backlog**: fecha o item 4 e CONCLUI o backlog pós-piloto → libera a
  release (diretriz do dono).
