# Design: painel-lista-encerramento

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
- HMD: FSM de 17 estados; `CaseEventType` é TextChoices (valor novo não
  exige migration); lock operacional em campos `locked_*` do `Case`;
  notificações por marcos (`create_milestone_notifications`); NIR vê
  resultados em "Meus casos" (spec case-closure).

## Goals / Non-Goals

- **Goal**: manager enxerga os casos do período (sem PHI) e consegue encerrar
  administrativamente qualquer caso não-CLEANED com motivo auditável.
- **Non-goal**: PHI na lista (decisão do dono, default NÃO); detail view p/
  manager; busca dinâmica; encerramento em lote; mudar fluxo clínico.

## Decisions

### D1 — Lista SSR sob as métricas, sem dados de paciente

`dashboard:home` estende o contexto: `cases` (QuerySet anotado p/ cards) +
filtros. Card = `agency_record_number` (ou uid curto 8 chars), status badge
(label do `CaseStatus`), tipos declarados (`procedures` com
`declared_by_nir`), `created_at`, resultado/decisão (imutáveis quando
existem), próximo passo (deriva do status). Filtros: `period` (reusado),
`scope=ativos|todos` (default `ativos` = `status != CLEANED`), `status`
(choices válidas), `q` (≥3 chars: nº registro exato/prefixo ou prefixo do
uid). Ordenação: `created_at` desc; paginação simples (25/pág) — sem
partial: o repo é SSR puro (forms GET re-renderizam a página).

### D2 — Encerramento administrativo (core, padrão ats-web adaptado)

`apps/cases/services.py::administratively_close_case(*, case, user,
active_role, reason_code, reason_text) -> Case`:
- Validações: `reason_code in ADMINISTRATIVE_CLOSURE_REASONS`,
  `reason_text.strip()` não vazio, `case.status != CLEANED`, `active_role in
  {"manager","admin"}` (defesa em profundidade — a rota já é `role_required`).
- FSM: `@transition(field="status", source=[todos exceto CLEANED],
  target=CaseStatus.CLEANED)` no modelo (método `administratively_close`),
  hook limpando lock (`locked_by/locked_at/locked_until/lock_token/
  lock_context/lock_role`) no mesmo atomic.
- Evento `CASE_ADMINISTRATIVELY_CLOSED` (CaseEventType novo; SEM migration —
  TextChoices) com payload `{reason_code, reason_text, by, role}`.
- Notificação ao criador: reuso da infra de marcos — novo gatilho no
  `create_milestone_notifications` (título/preview fixos, link → "Meus casos").
- Métrica do painel: contagem por eventos `CASE_ADMINISTRATIVELY_CLOSED` no
  período (espelho `admin_closed_case_ids` do ats-web).
- Intercorrência pós-agendamento: no HMD a reabertura por incidente é uma
  TRANSIÇÃO (não flag persistida) — não há o que limpar além do lock
  (diferença documentada vs ats-web).

### D3 — Rota/UI de encerramento (painel)

`dashboard:admin_close` (POST, `role_required("manager","admin")`,
`login_required` implícito): valida o caso (404 se inexistente), chama o
service, `messages.success` + redirect de volta ao painel com os filtros
preservados. UI: no card de cada caso não-CLEANED, form inline (botão
"Encerrar administrativamente" → página de confirmação simples
`dashboard/admin_close_confirm.html` com select do motivo + textarea
obrigatória) — padrão SSR do repo, sem JS novo.

### D4 — Resultado visível ao criador (Meus casos)

O presenter de "Meus casos" (intake) trata `CLEANED` com evento
administrativo como resultado "Encerrado administrativamente — {motivo}"
(sem expor `reason_text` cru ao NIR? Expor: é o criador do caso;
texto é do supervisor. Exibe código+texto) — MODIFIED da spec
case-closure com cenários verbatim preservados.

## Risks / Trade-offs

- Lista sem PHI pode limitar o manager (não sabe DE QUEM é o caso):
  registrado como decisão; flip futuro = policy change pontual (adicionar
  coluna re-identificada com role gate).
- Encerramento administrativo é destrutivo do ponto de vista de fluxo (caso
  não continua): mitigado por auditoria completa (evento + payload +
  notificação) e catálogo de motivos fixo.
- Busca por prefixo de uid/nº registro: índices existentes (PK +
  `agency_record_number` sem índice? verificar no slice; add db_index se
  necessário — SEM migration? `agency_record_number` já existe; índice novo
  = migration pequena aceitável no slice 1 se querying justificar).

## Open Questions

- **Owner**: a lista deve mostrar dados do paciente (nome/registro, como
  ats-web)? Default desta proposta: NÃO (postura da spec vigente). Flip =
  ajuste pontual no slice 2 antes da execução.
