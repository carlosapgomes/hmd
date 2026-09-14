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
  por caso — nº de registro/uid curto, badge de status, tipo(s) de
  procedimento declarado(s), criado em, decisão/resultado, próximo passo —
  SEM dados de paciente (postura vigente da spec dashboard; flip é decisão
  do dono, ver Open Question). Filtros: `?period` (reusado),
  `?scope=ativos|todos` (default ativos = status ≠ CLEANED), `?status=`
  opcional, busca server-side por nº de registro/prefixo de uid (≥3 chars,
  SSR puro, sem partial/HTMX).
- **Encerramento administrativo** (padrão ats-web adaptado): transição
  excepcional `qualquer status ≠ CLEANED → CLEANED`, disponível apenas na
  interface do painel (manager/admin — a rota já é `role_required`), com
  `reason_code` do catálogo fixo (`processing_error`, `llm_failure`,
  `system_bug`, `stuck_lock`, `duplicate_reprocess`, `other`) + `reason_text`
  obrigatório. Evento `CASE_ADMINISTRATIVELY_CLOSED` com payload auditável
  (código, texto, autor, papel); limpeza do lock operacional; notificação ao
  criador (NIR); contagem `administratively_closed` nas métricas do período;
  "Meus casos" (NIR) exibe o encerramento administrativo como resultado.

## Capabilities

### Modified: `dashboard`

- MODIFIED "Métricas por período sem dados de paciente" (ganha a contagem de
  encerramentos administrativos; cenários preservados) + ADDED "Lista de
  casos no painel sem dados de paciente".

### Modified: `case-closure`

- ADDED "Encerramento administrativo do caso" + MODIFIED "Resultado e casos
  encerrados visíveis ao criador" (cenários preservados; inclui o
  encerramento administrativo como resultado visível).

## Impact

- 1 migration mínima (nenhuma? — `CaseEventType` é TextChoices sem tabela;
  evento novo = valor novo; SEM migration de schema). Confirmação no design.
- Rotas novas: `dashboard:admin_close` (POST). Templates: `dashboard/home`
  ganha a lista + modal/form de confirmação.
- Zero mudança no fluxo clínico normal (decisão/negatura/ciência intactos).

## Não-goais

- Não adiciona dados de paciente à lista (decisão registrada; mudança futura
  é policy change do dono); não traz partial/HTMX/busca dinâmica (SSR do
  repo); não cria visão de detalhe de caso para manager (não existe; a lista
  é terminal); não encerra em lote.
