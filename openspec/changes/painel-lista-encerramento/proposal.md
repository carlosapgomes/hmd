# Proposal: painel-lista-encerramento

## Why

Uso real da fase 2: o painel gerencial mostra só métricas — o manager não vê
QUAIS casos estão em cada estado (a referência ats-web tem lista de casos com
filtros) e não existe forma de **encerrar um caso administrativamente**
(casos travados: FAILED por erro de processamento/LLM, lock travado,
duplicado por reenvio, etc. ficam permanentemente abertos). O próprio
incidente 3ca56d86 (FAILED) não tem encerramento. Isso é necessário para o
fluxo de trabalho do supervisor.

## What Changes

- **Lista de casos no painel** (`dashboard:home`, sob as métricas): cards
  por caso — nº de ocorrência (dado do caso) ou uid curto, badge de status,
  tipo(s) de procedimento declarado(s), criado em, decisão/resultado,
  próximo passo — SEM nome/nascimento do paciente (postura vigente; flip é
  decisão do dono, ver Open Question; invariante isolado no requisito da
  lista para o flip ser barato). Filtros: `?period` (reusado),
  `?scope=ativos|todos` (default ativos = status ≠ CLEANED), `?status=`
  opcional, busca server-side por nº de ocorrência/prefixo do uid (≥3
  chars, ignora abaixo disso; SSR puro, sem partial/HTMX), paginação 25/pág.
- **Encerramento administrativo** (padrão ats-web adaptado): transição
  excepcional `qualquer status ≠ CLEANED → CLEANED`, apenas na interface do
  painel (manager/admin — rota `role_required` + defesa no service), com
  `reason_code` do catálogo fixo (`processing_error`, `llm_failure`,
  `system_bug`, `stuck_lock`, `duplicate_reprocess`, `other`) +
  `reason_text` obrigatório. No mesmo atomic: snapshot + força-release do
  lock operacional (com evento de release), **minimização de dados clínicos**
  (mesma limpeza do CLEANED por ciência: documentos/anexos deletados,
  campos clínicos zerados) e eventos `CASE_STATUS_CLEANED` +
  `CASE_ADMINISTRATIVELY_CLOSED` (payload: código, texto, autor, papel,
  estado do lock). Recusa fail-closed quando há lease de worker viva;
  handlers de erro das tasks toleram caso CLEANED em voo. Notificação ao
  criador com **novo tipo de notificação** (migration de choices);
  contagem `administratively_closed` nas métricas do período (por timestamp
  do evento; sai de "em andamento"); "Meus casos" (NIR) exibe o
  encerramento administrativo como resultado.

## Capabilities

### Modified: `dashboard`

- MODIFIED "Métricas por período sem dados de paciente" (frase de página
  escopada à seção de métricas; cenários preservados verbatim + cenário
  novo de encerramentos) + ADDED "Lista de casos no painel" (com cenários
  de paginação/filtro/busca).

### Modified: `case-closure`

- ADDED "Encerramento administrativo do caso" (inclui minimização de dados
  clínicos e recusa durante processamento ativo) + MODIFIED "Resultado e
  casos encerrados visíveis ao criador" (cenários preservados; inclui o
  encerramento administrativo como resultado visível).

## Impact

- **1 migration** (`apps/accounts/migrations/0005_*.py`, AlterField das
  choices de `NotificationType` — sem SQL no Postgres; mantém
  `makemigrations --check` limpo). Nenhuma migration de schema/índice.
- Código: `apps/cases` (models/locks/services/events), `apps/accounts`
  (models/notifications/migration), guards nas 3 tasks
  (`apps/intake/tasks.py`, `apps/anonymization/tasks.py`,
  `apps/pipeline/orchestrator.py`), `apps/dashboard` (views/urls/métricas/
  templates), `apps/intake` (Meus casos).
- Amends declarados de testes existentes: "Página sem dados de paciente"
  (`test_views.py`) passa a pinnar nome do paciente (nº de ocorrência é
  dado do caso e aparece na lista); `test_metrics.py` ganha a chave nova.
- Zero mudança no fluxo clínico normal (decisão/negatura/ciência intactos).

## Não-goais

- Não adiciona nome/nascimento do paciente à lista (decisão registrada;
  mudança futura é policy change do dono); não traz partial/HTMX/busca
  dinâmica (SSR do repo); não cria visão de detalhe de caso para manager
  (não existe; vem com a trilha simplificada — backlog item 4, change
  futuro; a lista não trava esse futuro); não encerra em lote.
