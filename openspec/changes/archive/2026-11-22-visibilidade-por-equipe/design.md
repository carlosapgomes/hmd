# Design — visibilidade-por-equipe

## Context

Os três papéis operam como equipes, mas o escopo individual vive em **três camadas** (levantamento completo pós-review):

- **Views do intake (NIR)**: todas as views de leitura/operação filtram `created_by=user` — lista (`_build_my_cases_context`), detalhe ativo (`case_detail`), ciência (`case_ack`), PDFs (`pdf_viewer`, `attachment_pdf_viewer`, `serve_attachment`, `serve_document`), gate (`gate_release`, `gate_resubmit`) e confirmação de divergência (`procedure_review_confirm` — duas queries, linhas 1270 e 1364). O reenvio corrigido já é qualquer `nir` (change `reenvio-corrigido-qualquer-nir`), assim como a busca de encerrados.
- **Serviços (NIR)**: `_assert_owned_by` (`apps/intake/services.py:422`) roda DENTRO da transação sob `select_for_update` em `release_retained_case` (:515), `confirm_case_procedure_review` (:648) e `resubmit_case_documents` (:772); `acknowledge_case_receipt` (`apps/cases/closure.py:199-202`) rejeita não-criador com `ValueError`. Remover só o filtro da view deixaria as ações de escrita de NIR não-criador quebradas na camada de domínio.
- **Contadores do header**: `queue_counters` (`apps/accounts/context_processors.py:98,117`) calcula os badges «Decididos Hoje»/«Processados Hoje» com `*_decided_by=user`.
- **Doctor/Scheduler**: abas "hoje" filtram `doctor_decided_by=user` (`apps/doctor/views.py:242-246`) e `scheduling_decided_by=user` (`apps/scheduler/views.py:298-302`).
- **Gate de PDF/texto do agendador**: `can_view_pdf = case.scheduled_by_id == user.pk and status in PDF_ALLOWED_STATUSES` (`apps/scheduler/views.py:466,528,824,874`) — restringe a visualização do relatório original a quem processou o caso.

O incidente de produção (login break-glass → lista vazia → "casos sumiram") motivou a mudança; os carimbos `doctor_decided_by/at` e `scheduling_decided_by/at` (migrations 0013/0014) já existem e não mudam.

## Goals / Non-Goals

**Goals**

- Escopo de **equipe** nas superfícies dos três papéis: NIR (qualquer `nir` ativo em qualquer caso — views E serviços), «Decididos Hoje» (especialidade do visualizador, qualquer decisor), «Processados Hoje» (qualquer agendador).
- **PDF/texto original do detalhe do agendador em escopo de equipe** (decisão do dono 2026-11-22): qualquer agendador vê os cards e serve o documento em casos pós-decisão, mantido o gate por estado.
- Decisor visível nos cards das abas "hoje"; badges do header coerentes com as abas (equipe).
- Auditoria intacta: atores continuam gravados por ação (ciência/gate/confirmação registram o operador real).

**Non-Goals**

- Fan-out de **notificações** (`UserNotification`): permanece criador-targeted nas respostas finais e fan-out schedurers — item de backlog registrado (a equipe vê o caso pela lista; o sino individual não é o canal do time).
- Abas Pendentes (já globais), FSM, fluxo assíncrono, backfills, presenters de detalhe médico/agendador (exceto o gate de PDF acima), fronteira LLM/anonização.
- Filtro "por decisor" adicional nas abas "hoje" (se surgir necessidade, change futura).

## Decisions

- **D1 — Guard de papel é o único gate do intake, em view E serviço.** Remoção de `created_by=user` dos querysets das views; remoção do `_assert_owned_by` dos três serviços e do check de criador em `acknowledge_case_receipt` (o operador segue gravado como ator do evento). Defesa em profundidade: os serviços de gate/ciência/confirmação ganham re-check de papel `nir` dentro da transação — padrão que `confirm_case_procedure_review` já tem em `apps/intake/services.py:649-653` — incluindo `acknowledge_case_receipt`; a revogação de papel com sessão vigente (`active_role` stalado) responde `PermissionError`, e as views `gate_release`/`gate_resubmit`/`case_ack` passam a traduzir `PermissionError → PermissionDenied` (403), espelhando `procedure_review_confirm` (`apps/intake/views.py:1324-1325`) — nunca 500. 404 apenas para caso inexistente; papel não-`nir` continua barrado (`role_required("nir")` + intranet ADR-0002). O `is_own` de «Reenvios corrigidos» deixa de particionar links; o redirect de recusa do reenvio unifica para o detalhe do caso ativo (qualquer `nir` o alcança — remove o ramo residual `elif case.created_by_id == user.pk` em `case_resubmit`).
- **D2 — Rótulo «Casos em Processamento» em TODAS as superfícies.** Nav, header/título da lista (`<h2>` de `_my_cases_content.html`), estado vazio, alerta pós-lote de `home.html:58`, breadcrumb de `case_detail.html:15` e manual do usuário (seções NIR **e** Médico **e** Agendador — as três descrevem escopo individual hoje). Convenção de capitalização (padrão das demais páginas do app): nav e `<h2>` em title case («Casos em Processamento», como «Meus Casos» hoje); `<title>`/`<h1>` da página em sentence case («Casos em processamento», como «Casos aguardando decisão» da fila médica) — testes de título fazem assert exato e devem seguir isso. Universo inalterado: ativos (não-`CLEANED`), agora de qualquer criador; `?tab=closed` legado preserva o fallback; contador do header conta todos os ativos da equipe. Spec `ui-shell` atualizada (desvio deliberado do vocabulário ats-web).
- **D3 — Abas "hoje" substituem o escopo individual (sem segunda aba "minhas")**; a checagem individual é atendida pelo **decisor visível no card**. Rows com decisor nulo continuam fora: a troca é `*_decided_by=user` → `*_decided_by__isnull=False` (contrato fail-closed das migrations 0013/0014 preservado), com `select_related("doctor_decided_by")`/`select_related("scheduling_decided_by")` nas queries das abas para renderizar o decisor sem N+1.
- **D4 — «Decididos Hoje» mantém o predicado de subtipo do visualizador** (`_accessible_cases`); o alargamento é só do filtro de decisor. Os **contadores do header** (`queue_counters`) seguem a mesma regra (`isnull=False` + matriz de subtipo no doctor), coerentes com as abas.
- **D5 — Ciência e resultado em escopo de equipe** (spec `case-closure` coberta): qualquer `nir` registra a ciência de `FINAL_REPLY_POSTED` (validação de estado permanece — caso já encerrado segue recusado com mensagem nomeada, nunca 404 de escopo).
- **D6 — Anti-drift de testes**: cenários de "caso alheio negado" e "aba escopada ao decisor" são **invertidos** (alheio visível/decisões de colega listadas), não deletados. Testes diretos de serviço (não só de view) cobrem NIR não-criador em gate/confirmação/ciência.
- **D7 — Gate de PDF/texto do agendador vira por decisão de agendamento + estado, não por decisor** (decisão do dono 2026-11-22): `can_view_pdf = case.scheduling_decided_at is not None and case.status in PDF_ALLOWED_STATUSES`. A guarda de decisão é OBRIGATÓRIA: `PDF_ALLOWED_STATUSES` inclui `FINAL_REPLY_POSTED`/`AWAITING_NIR_ACK`, e negativa médica total chega nesses estados **sem nunca ter passado pelo agendamento** (`scheduled_by`/`scheduling_decided_*` nulos — `post_doctor_denial_reply` leva `DOCTOR_DENIED → FINAL_REPLY_POSTED` direto); status sozinho exporia PDF/texto desses casos a qualquer agendador. Com a guarda, o gate cobre confirmação **e** negação de agendamento (qualquer decisor da equipe) em estados pós-decisão; casos de negativa médica continuam sem cards e com 404 nas rotas (teste negativo explícito na task 3.3). Specs `scheduling` («Visão do agendador limitada ao necessário» **e** «Histórico do agendador») e `pdf-original-viewer` atualizadas; `attachments` idem para o NIR (serve de anexo de qualquer criador).
- **D8 — Títulos de cenários antitéticos são intencionais.** O validador do OpenSpec casa requisitos MODIFIED por nome; renomear título = drop+add silencioso. Cenários como «Lista mostra apenas casos do próprio NIR» e «Decididos Hoje inclui apenas as próprias decisões» preservam o título histórico com corpo invertido — o implementador deve ler o CORPO, não o título.

## Risks / Trade-offs

- **PHI**: o alargamento é estritamente intra-papel (nir↔nir, agendador↔agendador; médico segue filtrado por `DoctorAccess`). O PDF/texto original no detalhe do agendador amplia PHI do relatório à equipe de agendamento — aceite explícito do dono (2026-11-22): a equipe de agendamento já acessa o prontuário eletrônico completo do hospital; no HMD o alargamento fica limitado a casos **com decisão de agendamento registrada** (D7), fronteira LLM/anonização intocada (a re-identificação continua na renderização).
- **Operação concorrente**: dois `nir` podem operar gate/confirmação/ciência do mesmo caso — mitigações vigentes confirmadas no código (`select_for_update` + validação de retenção/estado em `apps/intake/services.py:514-529,771-774`; digests `v1:` + `latest_review_event` + lock com 400/409 em `:643-698`; `select_for_update` + `FINAL_REPLY_POSTED` em `apps/cases/closure.py:193-198`).
- **Zero migrations**: nenhum backfill — carimbos de decisor já existem. Rollback = revert do código.

## Migration Plan

Sem migrações de dados. Deploy único; `created_by`/`scheduled_by` passam a ser autoria/auditoria, não predicado de acesso.

## Open Questions

Nenhuma bloqueante. Backlog registrado: fan-out de notificações por equipe (Non-Goals).
