# Design: painel-lista-encerramento (rev.3 — plan review)

## Context

- Painel atual (`apps/dashboard/views.py::home`): métricas puras por período
  (`hoje|7d|30d|tudo`), SEM dados de paciente (spec dashboard vigente);
  acesso `manager`/`admin` (`role_required`).
- ats-web (referência): lista de casos com filtros server-side; e
  `administratively_close_case(*, case, user, reason_code, reason_text,
  active_role)` — transição excepcional para CLEANED só manager/admin,
  catálogo de 6 motivos, evento `CASE_ADMINISTRATIVELY_CLOSED` auditável,
  limpa lock com snapshot no payload, métrica separada no painel.
- HMD: FSM de 17 estados (`apps/cases/models.py` — hooks privados
  `_fsm_*` + operações públicas via `_run_transition`; NÃO existe
  `apps/cases/services.py` — serviços de fechamento vivem em
  `apps/cases/closure.py`, dono também de `_clean_acknowledged_clinical_data`
  e do padrão de deleção física: coletar `file.name` ANTES do delete e
  `transaction.on_commit(_delete_files_best_effort)`); lock operacional em
  `apps/cases/locks.py` (`release_case_lock` re-lê a linha sob
  `select_for_update` e muta a instância; lease comparada com
  `timezone.now()` aware); notificações por marcos
  (`create_milestone_notifications`; choices congeladas na migration
  `0004`); spec notifications enumera 3 marcos com textos canônicos
  fixos; manual do usuário enumera os marcos (`templates/accounts/
  manual.html`).
- Backlog pós-piloto (PROJECT_CONTEXT): a listagem do painel hospedará, em
  change futuro, o detail view com a trilha simplificada — este change cria
  a listagem; detail é aditivo depois.

## Goals / Non-Goals

- **Goal**: manager enxerga os casos do período (sem nome/nascimento do
  paciente) e encerra administrativamente qualquer caso não-CLEANED com
  motivo auditável, minimização completa (rows E arquivos físicos) e sem
  que workers em voo reescrevam dados clínicos.
- **Non-goal**: nome/nascimento do paciente na lista (decisão do dono,
  default NÃO); detail view p/ manager (vem com a trilha, change futuro);
  busca dinâmica/partial; encerramento em lote; mudar fluxo clínico.

## Decisions

### D1 — Lista SSR sob as métricas, sem dados de paciente

`dashboard:home` estende o contexto: `cases` (QuerySet anotado p/ cards) +
filtros. Card = `agency_record_number` (**nº de ocorrência — dado do caso,
exibido deliberadamente ao manager**; a spec anonymization o trata como
identificador a tokenizar nos textos, mas na UI do painel ele identifica o
CASO, não o paciente) ou uid curto 8 chars, status badge, tipos declarados,
`created_at`, resultado/decisão (imutáveis quando existem), próximo passo.
Filtros: `period` (reusado), `scope=ativos|todos` (default `ativos` =
`status != CLEANED`), `status` (choices válidas), `q` (≥3 chars:
`agency_record_number__icontains` OU `case_id__istartswith` — o backend
Postgres do Django emite `::text` para UUID sozinho; **sem índice novo**:
`icontains` não usa b-tree e a escala do piloto tolera seq scan; `pg_trgm`
já existe se escalar). Ordenação `created_at` desc; paginação 25/pág; SSR
puro.

**Invariante zero-PHI isolado e escopado**: o cenário "Página sem dados de
paciente" (MODIFIED dashboard) tem o THEN escopado à **seção de métricas**
("na seção de métricas, nenhum nome ou número de registro de paciente
aparece" — o repo historicamente trata o nº de ocorrência como registro no
painel; a lista o exibe deliberadamente); o requisito novo da lista carrega
o invariante próprio — **nome E data de nascimento do paciente não aparecem
na lista** (pinados nos testes do slice 002; o amend de `test_views.py`
troca o pino de `agency_record_number` para `patient_name` +
`patient_birth_date` e atualiza o docstring). Flip futuro (backlog 1-2) =
editar UM requisito + presenter.

### D2 — Encerramento administrativo (core, padrão ats-web adaptado)

Service em **`apps/cases/closure.py`** (módulo dono dos serviços de
fechamento; reuso do helper de minimização NO MESMO módulo — sem import
privado): `administratively_close_case(*, case, user, active_role,
reason_code, reason_text) -> Case`.
- Validações: `reason_code in ADMINISTRATIVE_CLOSURE_REASONS` (catálogo
  constante em `closure.py`: `processing_error/llm_failure/system_bug/
  stuck_lock/duplicate_reprocess/other` + labels pt-BR),
  `reason_text.strip()` não vazio, `case.status != CLEANED`, `active_role in
  {"manager","admin"}` (defesa em profundidade), e **recusa com lease de
  worker viva**: `locked_until > timezone.now()` (aware, espelhando
  `locks.py`) e `lock_context` começando com `worker_` → `ValueError`
  (padrão do repo; NÃO existe `DomainError`) com mensagem "caso em
  processamento; aguarde a task terminar ou trate o lock travado".
- FSM no MODELO (padrão do repo): hook privado
  `_fsm_administratively_close` (decorado `@transition(source=[todos
  exceto CLEANED], target=CLEANED)`; precedentes `_fsm_fail_processing`
  4 sources, `_fsm_post_final_reply` 3) + **op pública**
  `Case.administratively_close(*, user, role, reason_code, reason_text,
  lock_snapshot)` que chama `_run_transition` e grava o evento
  `CASE_ADMINISTRATIVELY_CLOSED` com payload `{reason_code, reason_text,
  by: username, role, had_lock, previous_lock_context, previous_lock_until}`.
  Nenhum service chama `_run_transition` direto. O `lock_snapshot` (dict
  montado pelo service no passo (1), ANTES do force-release limpar a
  instância) é parâmetro porque no passo da transição os campos de lock da
  instância já estão zerados — sem ele o payload não teria fonte.
- **No atomic do service, nesta ordem**: (1) snapshot dos campos de lock
  para o payload; (2) `force_release_case_lock(case, *,
  reason="administrative_closure", user=...)` — função pública NOVA em
  `apps/cases/locks.py` que espelha `release_case_lock` (re-lê a linha sob
  `select_for_update` e **copia os campos limpos de volta para a instância
  do chamador** — o `save()` full posterior da transição NÃO ressuscita o
  lock) e grava `CASE_LOCK_RELEASED` com payload no padrão `_expired_payload`
  (`{reason, forced: true, previous_lock_context, previous_lock_until,
  by, role}` — autor na trilha = quem encerou); (3) coletar `file.name` de documentos/anexos ANTES do
  delete (padrão do ack) e **minimização completa** via
  `_clean_acknowledged_clinical_data` (rows) no mesmo módulo; (4) op pública
  do modelo (recebendo o `lock_snapshot` do passo (1)) → `CASE_STATUS_CLEANED` + `CASE_ADMINISTRATIVELY_CLOSED`
  (transição `save()` com campos de lock já limpos na instância); (5)
  `transaction.on_commit(lambda: _delete_files_best_effort(file_names))` —
  **arquivos físicos removidos do storage**, paridade real com o
  encerramento por ciência (spec case-closure vigente: "rows e arquivos").
- **Eventos: exatamente 2** por encerramento (`CASE_STATUS_CLEANED` +
  `CASE_ADMINISTRATIVELY_CLOSED`; +1 `CASE_LOCK_RELEASED` quando havia
  lock) — pinado em teste com `refresh_from_db`.
- **Workers em voo — três camadas** (a janela é criada por ESTE change:
  antes dele nenhum caso ia a CLEANED fora de FINAL_REPLY_POSTED):
  (a) recusa por lease viva cobre o caso comum; (b) abort em status
  CLEANED após cada `refresh_from_db` pré-passo e no `except` das 3
  tasks/orquestrador (`apps/intake/tasks.py`,
  `apps/anonymization/tasks.py`, `apps/pipeline/orchestrator.py` — os
  refresh já existem); (c) **releitura de status DENTRO dos atomics de
  escrita**, cobrindo o passo longo com lease expirada que retorna depois
  do encerramento: `apps/pipeline/policy.py` (~679: re-lê no atomic,
  retorna `{}` sem persistir se CLEANED — orquestrador ignora o retorno,
  mypy satisfeito), `llm2_service.py` (~428, idem), `prior_case.py`
  (~218, idem); **`apps/intake/tasks.py`** (~218-263: `_extract_and_decide`
  re-lê a linha dentro do atomic ANTES de gravar `extracted_text`/eventos —
  o caminho de RETENÇÃO hoje commita via `return False` no release, então
  o gate tem de vir antes de qualquer write, inclusive o evento
  `manual_review_required`); **`apps/anonymization/tasks.py`** (~160:
  re-ler ANTES de `anonymize_case_text`/`complete_anonymization` — o
  `case.save()` full do serviço ressuscitaria `status`, campos clínicos e
  os 6 campos de lock lidos antes do claim). Worker de anexos NÃO precisa
  de gate (só escreve na row do anexo; re-lê e devolve None se a row
  sumiu — resíduo pré-existente absorvido por `_fail_attachment`).
  Testes pinnados com INTERCALAÇÃO (transição para CLEANED entre o refresh
  pré-passo e a escrita — sem intercalar, o gate de topo no-opa e o teste
  fica verde sem fix): intake e anonymization + policy/llm2/prior_case.
- Notificação ao criador: **novo `NotificationType`
  `ADMINISTRATIVELY_CLOSED = "administratively_closed"`** (24 chars <
  max_length 30; choices congeladas na migration `0004` → **migration
  `0005` AlterField**, sem DDL no Postgres, mantém `makemigrations
  --check` limpo; amend do teste-invariante no mesmo slice). Gatilho no
  `create_milestone_notifications` para `CASE_ADMINISTRATIVELY_CLOSED` →
  destinatário `case.created_by`; **texto 100% fixo** (título e preview
  "Caso encerrado administrativamente" — sem `reason_text`, sem PHI),
  link pelo destino centralizado (`nir` → `intake:case_detail`).
  Delta da spec **notifications** (4º marco) + manual do usuário
  atualizado (`templates/accounts/manual.html` enumera marcos; docstring
  do módulo deixa de dizer "conjunto FECHADO").
- Métrica (coerência de populações): card `administratively_closed` =
  contagem de eventos `CASE_ADMINISTRATIVELY_CLOSED` com `timestamp` na
  janela (semântica do cenário "dentro do período"); **`em_andamento`
  continua derivado da POPULAÇÃO** (`created_at` na janela):
  `em_andamento = |population − agendados − negados − (admin_fechados ∩
  population sem desfecho)|` — admin-fechado FORA da população não subtrai
  (evita negativo com `total=0`), admin-fechado COM desfecho (agendado) já
  está em `agendados` (sem dupla subtração). Testes para os dois casos
  limítrofes.
- Intercorrência pós-agendamento: no HMD a reabertura por incidente é uma
  TRANSIÇÃO — não há flag a limpar além do lock.
- "Próximo passo" do card: fonte canônica única — mapa
  `CASE_NEXT_STEP_LABELS` (17 estados → pt-BR) em módulo novo
  `apps/dashboard/case_labels.py`, com teste de cobertura completa dos
  estados (nada inventado inline no template).
- `encerrados` (CLEANED) CONTINUA contando os administrativamente
  encerrados (mesmo card de encerrados de hoje).
- Arquivamento: além do desvio vs PROJECT_CONTEXT 4(b), ajustar o
  **Purpose** da spec dashboard main ("sem qualquer dado de paciente" →
  métricas sem dados de paciente; lista identifica casos por nº de
  ocorrência).

### D3 — Rota/UI de encerramento (painel)

`dashboard:admin_close_confirm` (GET: select do catálogo + textarea
obrigatória) e `dashboard:admin_close` (POST: service +
`messages.success` + redirect preservando filtros; validação e a recusa por
lease viva — `ValueError` — re-renderizam com `messages.error`; proibido
`HttpResponseForbidden(render(...))` aninhado). 403 paramétrico para
nir/doctor/scheduler; 404 caso inexistente. Ação no card de cada caso
não-CLEANED. Padrão SSR, sem JS novo.

### D4 — Resultado visível ao criador (Meus casos)

O queryset de "Meus casos" ganha `prefetch_related("events")` (hoje só
`procedures`) para derivar o resultado sem N+1: `CLEANED` + evento
administrativo → "Encerrado administrativamente — {label do motivo}"
(código + texto: o criador é o dono do caso e o texto é do supervisor).
Template `templates/intake/my_cases.html` renderiza o resultado na aba de
encerrados.

## Correções de testes existentes (declaradas)

- `apps/dashboard/tests/test_views.py:33-36/141-155` ("Página sem dados de
  paciente"): passa a pinnar **nome e data de nascimento do paciente**
  ausentes (nº de ocorrência é dado do caso e aparece na lista); docstring
  atualizado. Amend no slice 002.
- `apps/dashboard/tests/test_metrics.py` (`EMPTY_SUMMARY` ~45-51 e
  iguadades de dict ~144-150/186-192/203-209/418-424): ganham a chave
  `administratively_closed`. Amend declarado no slice 002.

## Risks / Trade-offs

- Lista sem nome do paciente limita o manager: decisão registrada; flip
  barato (invariante isolado). Backlog itens 1-2 indicam que o flip vem.
- Minimização apaga texto bruto e ARQUIVOS de casos FAILED (ex.: evidência
  do 3ca56d86): aceito — encerramento é terminal, mesmo racional do
  CLEANED por ciência; a trilha de eventos (sem PHI) permanece.
- Validação de status dentro dos atomics do pipeline: custo de um re-read
  por escrita — barato e fecha a janela do worker-zumbi.
- Busca `icontains` sem índice: seq scan aceitável no piloto; trigram
  disponível.
- "Recusa por lease viva" pode frustrar em lock travado COM lease longa:
  leases do HMD são curtas (300s default) e `stuck_lock` cobre a expirada.

## Open Questions

- **Owner**: a lista deve mostrar dados do paciente (nome/registro, como
  ats-web)? Default desta proposta: NÃO (postura da spec vigente). Flip =
  ajuste pontual no slice 002 antes da execução.
