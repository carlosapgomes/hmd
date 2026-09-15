# Design: painel-lista-encerramento (rev.2 — plan review)

## Context

- Painel atual (`apps/dashboard/views.py::home`): métricas puras por período
  (`hoje|7d|30d|tudo`), SEM dados de paciente (spec dashboard vigente);
  acesso `manager`/`admin` (`role_required`).
- ats-web (referência): lista de casos com patient_name/registro/idade,
  filtros escopo/datas/busca server-side + partial dinâmico; e
  `administratively_close_case(*, case, user, reason_code, reason_text,
  active_role)` — transição excepcional para CLEANED só manager/admin,
  catálogo `ADMINISTRATIVE_CLOSURE_REASONS` (6 códigos), evento
  `CASE_ADMINISTRATIVELY_CLOSED` auditável, limpa lock, métrica separada no
  painel (`admin_closed_case_ids`).
- HMD: FSM de 17 estados; `CaseEventType` é TextChoices (evento novo não
  exige migration); lock operacional em campos `locked_*` do `Case`
  (`apps/cases/locks.py`); notificações por marcos
  (`create_milestone_notifications`, signal `CaseEvent.post_save`);
  minimização de dados clínicos existe em `apps/cases/closure.py::
  _clean_acknowledged_clinical_data` (cadeia do ack: deleta documentos/
  anexos e zera campos clínicos); NIR vê resultados em "Meus casos".
- Backlog pós-piloto (PROJECT_CONTEXT): a listagem do painel hospedará, em
  change futuro, o detail view com a trilha de eventos simplificada — este
  change cria a listagem; detail é aditivo depois.

## Goals / Non-Goals

- **Goal**: manager enxerga os casos do período (sem PHI de paciente) e
  consegue encerrar administrativamente qualquer caso não-CLEANED com
  motivo auditável, minimização de dados e sem quebrar workers em voo.
- **Non-goal**: nome/nascimento do paciente na lista (decisão do dono,
  default NÃO); detail view p/ manager (vem com a trilha, change futuro);
  busca dinâmica/partial; encerramento em lote; mudar fluxo clínico.

## Decisions

### D1 — Lista SSR sob as métricas, sem dados de paciente

`dashboard:home` estende o contexto: `cases` (QuerySet anotado p/ cards) +
filtros. Card = `agency_record_number` (**nº de ocorrência — dado do caso,
não do paciente**; ou uid curto 8 chars quando ausente), status badge,
tipos declarados, `created_at`, resultado/decisão (imutáveis quando
existem), próximo passo (deriva do status). Filtros: `period` (reusado),
`scope=ativos|todos` (default `ativos` = `status != CLEANED`), `status`
(choices válidas), `q` (≥3 chars: `agency_record_number__icontains` OU
`case_id__istartswith` — o backend Postgres do Django emite `::text` para
UUID, sem cast manual; **sem índice novo**: `icontains` não usa b-tree e a
escala do piloto tolera seq scan; `pg_trgm` já existe no banco se um dia
precisar). Ordenação `created_at` desc; paginação 25/pág; SSR puro (forms
GET re-renderizam).

**Invariante zero-PHI isolado**: o requisito de métricas (MODIFIED) ganha a
frase escopada — "a **seção de métricas** contém apenas contagens/tempos/
labels" — e o requisito novo da lista carrega o próprio invariante ("nome e
data de nascimento do paciente não aparecem na lista"). Assim o flip futuro
(backlog itens 1-2: dados do paciente nas filas/lista) é edição de UM
requisito + presenter, sem tocar o de métricas. Nota de arquivamento:
registrar o desvio de escopo vs PROJECT_CONTEXT item 4(b) (trilha viverá no
detail do painel — change futuro aditivo).

### D2 — Encerramento administrativo (core, padrão ats-web adaptado)

`apps/cases/services.py::administratively_close_case(*, case, user,
active_role, reason_code, reason_text) -> Case`:
- Validações: `reason_code in ADMINISTRATIVE_CLOSURE_REASONS`,
  `reason_text.strip()` não vazio, `case.status != CLEANED`, `active_role in
  {"manager","admin"}` (defesa em profundidade — a rota já é
  `role_required`), e **recusa com lease de worker viva**: se
  `locked_until > now()` e `lock_context` começa com `worker_`, levanta
  `DomainError` ("caso em processamento; aguarde ou trate o stuck lock") —
  sem essa guarda, a task em voo escreveria sobre um caso CLEANED.
- FSM: `@transition(field="status", source=[todos exceto CLEANED],
  target=CaseStatus.CLEANED)` (método `administratively_close`) — válido:
  precedentes `_fsm_fail_processing` (4 sources) e `_fsm_post_final_reply`
  (3 sources); não há transição saindo de `FAILED` hoje (nenhuma
  ambiguidade).
- **No mesmo atomic, nesta ordem**: (1) snapshot do lock no payload
  (`had_lock`, `previous_lock_context`, `previous_lock_until` — evidência
  para o motivo `stuck_lock`, padrão ats-web); (2) força-release do lock
  pela função pública nova em `apps/cases/locks.py`
  (`force_release_case_lock(case, reason="administrative_closure")`) que
  limpa os campos E grava `CASE_LOCK_RELEASED` com payload de força —
  single-writer preservado (helper vive no locks.py, não duplicado em hook
  de modelo); (3) **minimização de dados clínicos** reutilizando a limpeza
  da cadeia do ack (deleta `CaseDocument`/`CaseAttachment` e zera
  `extracted_text/anonymized_text/pseudonym_map/structured_data/
  summary_text/suggested_action/policy_result`) — encerramento é terminal:
  mesmo racional de privacidade do CLEANED por ciência, e fecha o buraco de
  `intake:serve_document` servir PDF de caso concluído; (4) `_run_transition`
  → grava `CASE_STATUS_CLEANED` (projeção automática) e o evento
  `CASE_ADMINISTRATIVELY_CLOSED` com payload `{reason_code, reason_text,
  by: username, role, had_lock, previous_lock_context}`.
- **Eventos: exatamente 2** por encerramento (`CASE_LOCK_RELEASED` extra
  apenas quando havia lock): pinado em teste (`events_before + 2`).
- **Tasks em voo (defesa)**: os handlers de erro das 3 tasks
  (`apps/intake/tasks.py`, `apps/anonymization/tasks.py`,
  `apps/pipeline/orchestrator.py`) fazem `refresh_from_db` no `except` e,
  se o status fresco é `CLEANED`, retornam sem chamar `fail_processing`
  (transição impossível a partir de CLEANED explodiria na task do django-q2
  mesmo após a recusa por lease viva cobrir o caso comum).
- Notificação ao criador: **novo `NotificationType`**
  `ADMINISTRATIVELY_CLOSED = "administratively_closed"` (choices são
  congeladas na migration `0004` → **migration `0005` AlterField**, sem SQL
  no Postgres, mantém `makemigrations --check` limpo; teste-invariante dos
  "3 marcos" em `test_notifications_model.py` é atualizado no mesmo slice).
  Gatilho no `create_milestone_notifications` para
  `CASE_ADMINISTRATIVELY_CLOSED` → destinatário `case.created_by`, link pelo
  destino centralizado existente (`nir` → `intake:case_detail` — renderiza
  casos CLEANED).
- Métrica: chave nova `administratively_closed` no dict de
  `compute_summary`, **por timestamp de evento** (`CASE_ADMINISTRATIVELY_
  CLOSED` na janela do período — consistente com o cenário "encerrados administrativamente
  dentro do período", enquanto total/agendados/negados seguem por
  `created_at`); `em_andamento = total − agendados − negados −
  administratively_closed` (caso admin-fechado não é "em andamento" —
  coerência do cenário "Resumo do período").
- Intercorrência pós-agendamento: no HMD a reabertura por incidente é uma
  TRANSIÇÃO (não flag persistida) — não há o que limpar além do lock.

### D3 — Rota/UI de encerramento (painel)

`dashboard:admin_close_confirm` (GET: formulário select do catálogo +
textarea) e `dashboard:admin_close` (POST: service + `messages.success` +
redirect preservando filtros; erros — inclusive a recusa por lease viva —
re-renderizam com `messages.error`; sem `HttpResponseForbidden(render(...))`
aninhado). 403 paramétrico para nir/doctor/scheduler; 404 caso inexistente.
Ação no card de cada caso não-CLEANED. Padrão SSR, sem JS novo.

### D4 — Resultado visível ao criador (Meus casos)

O presenter de "Meus casos" (hoje inline em `apps/intake/views.py` — não
existe `apps/intake/presenters.py`) trata `CLEANED` + evento administrativo
como resultado "Encerrado administrativamente — {label do motivo}" (código +
texto: o criador é o dono do caso e o texto é do supervisor). Template
`templates/intake/my_cases.html` renderiza o resultado na aba de encerrados.

## Correções de testes existentes (declaradas)

- `apps/dashboard/tests/test_views.py:141-155` ("Página sem dados de
  paciente"): hoje pina que o `agency_record_number` NÃO aparece — com a
  lista na página (nº de ocorrência é dado do caso), o teste passa a pinnar
  a ausência de **nome do paciente** (invariante real da spec; não há
  "número de registro do paciente" no HMD, só o nº de ocorrência da
  agência). Amend no slice 002, com o desvio declarado.
- `apps/dashboard/tests/test_metrics.py` (`EMPTY_SUMMARY` e iguadades de
  dict exato): ganham a chave `administratively_closed` — amend declarado
  no slice 002.

## Risks / Trade-offs

- Lista sem nome do paciente limita o manager (não sabe DE QUEM é o caso):
  decisão registrada; flip barato (invariante isolado no requisito da
  lista). Backlog itens 1-2 indicam que o flip vem.
- Minimização no encerramento apaga o texto bruto de casos FAILED (ex.:
  evidência do 3ca56d86): aceito — encerramento é terminal e o racional de
  privacidade é o mesmo do CLEANED por ciência; a trilha de eventos
  (status/contagens, sem PHI) permanece como evidência.
- Busca `icontains` sem índice: seq scan aceitável na escala do piloto;
  trigram disponível (`docker/init.sql`) se escalar.
- Semântica "recusa com lease viva" pode frustrar o supervisor em lock
  travado COM lease longa: os locks do HMD têm `lease_seconds` curto e o
  motivo `stuck_lock` cobre o caso de lease expirada (permitida).

## Open Questions

- **Owner**: a lista deve mostrar dados do paciente (nome/registro, como
  ats-web)? Default desta proposta: NÃO (postura da spec vigente). Flip =
  ajuste pontual no slice 002 antes da execução.
