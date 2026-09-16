# Design: case-work-lock

Espelho consciente do desenho do ATS Web (em produção), adaptado ao HMD.
Referência de implementação (ler ao duvidar de semântica):
`/projects/dev/ats-web/apps/cases/services.py` (lock service),
`apps/doctor/views.py` (decision/submit/renew/release),
`static/js/work_lock.js` (heartbeat), `templates/doctor/_queue_content.html`
(cards com reserva).

## D1 — Página de trabalho única (médico)

- `doctor:case_decide` (GET) passa a ser a **página de trabalho**: renderiza
  todo o contexto de leitura (o presenter `build_case_detail_context` já é
  usado ali hoje) **e** o formulário como último bloco.
- Os cards de leitura saem de `templates/doctor/case_detail.html` para um
  partial `templates/doctor/_case_cards.html`, incluído pela página de
  trabalho e pelo detalhe read-only — ordem única para os dois.
- **Ordem dos cards** (definida pelo dono — leitura clínica antes da síntese,
  síntese antes da decisão): Identificação do paciente → Procedimentos do caso
  → Sumário clínico → Estrutura extraída → Prior-case → Documentos → Anexos →
  Requisitos gerais acionáveis → Recomendação agregada do pipeline →
  **Alertas consultivos** → formulário.
  («Decisões registradas» fica FORA do partial — só no detalhe read-only,
  pós-decisão.)
- Topo da página: âncora «Ir para o formulário de decisão»
  (`href="#doctor-decision-form"`) no primeiro card — navegação interna, não
  rota nova. Banner de erro de validação do POST também aponta para a âncora
  (padrão ATS).
- Fim do formulário: [Registrar decisão] (submit) + [Sair sem decidir] lado a
  lado (`d-flex gap-2`). No slice 001 o «Sair sem decidir» é link para a fila;
  no slice 003 passa a liberar a reserva (server-side).
- `doctor:case_detail` permanece como **detalhe read-only** (casos decididos,
  aba decididos, breadcrumb) — inclui o mesmo partial + «Decisões registradas».
- Fila médica (aba aguardando): ação primária do card leva a `case_decide`
  («Avaliar»), não mais ao detalhe.

## D2 — Reserva de caso (core)

Mecânica já existente em `apps/cases/locks.py` (claim/assert/release/renew com
`select_for_update`, eventos `CASE_LOCK_*` na trilha). Ajustes:

1. **Lease por contexto** (padrão ATS `_CONTEXT_LEASE_SETTINGS`):
   `doctor_decision` → setting `CASE_LOCK_LEASE_SECONDS_DOCTOR` (default
   **3600s** — leitura clínica longa). `scheduler_confirm` e `worker` seguem o
   default `CASE_LOCK_LEASE_SECONDS` (300s). Precedência: override explícito >
   setting por contexto > default.
2. **`compute_lock_display(case, user)`** em `locks.py`: dict
   `is_locked` / `is_locked_by_current_user` / `locked_by_display` /
   `locked_until` / `lock_context` (lock vencido = não travado).
3. **`expire_stale_locks_for_statuses(statuses)`**: expiração lazy filtrada
   por status, chamada antes de montar as filas (a `expire_stale_locks`
   genérica permanece para uso futuro/ferramentas). Sem eventos (padrão ATS) —
   a trilha auditável continua vindo dos eventos de claim/release/tomada.
4. **Claim na abertura**: `case_decide` (GET) faz
   `claim_case_lock(case, user, context="doctor_decision", role="doctor")`
   após o guard de acesso e a checagem de status `AWAITING_DOCTOR`.
   - Não adquiriu (outro portador) → `messages.warning`
     «Este caso está reservado por {nome}. Aguarde até que a reserva expire
     para acessá-lo.» + redirect à fila (nunca 500).
   - Adquiriu → token no contexto e `<input type="hidden" name="lock_token">`
     no formulário. Mesmo médico reabrindo = renovação com token novo
     (comportamento do claim atual).
5. **POST exige posse — validada sob row lock**: `case_decide` (POST) lê
   `lock_token`; a view abre `transaction.atomic()` e re-lê a linha com
   `Case.objects.select_for_update().get(pk=...)` ANTES de qualquer escrita,
   valida `assert_case_lock(locked, token)` sobre a linha recém-travada
   (time-of-check == time-of-use, mesma técnica do `locks.py`) e só então
   chama o serviço (o `atomic` do serviço vira savepoint da transação da
   view) e faz o release determinístico — **tudo na mesma transação**, sem
   janela de expiração/tomada entre validação e persistência.
   - Sem token, token inválido ou lease vencida → re-render do formulário
     com
     `lock_error` («Sua reserva para este caso expirou ou não é válida.
     Volte para a fila e tente novamente.») — **nada persiste** (o serviço
     `record_doctor_procedure_decisions` não é chamado).
   - Posse válida → serviço atômico existente → **release determinístico**
     (`release_case_lock`, mesma transação) → redirect ao detalhe com flash
     de sucesso.
   - A proteção contra corrida permanece dupla: FSM + `TransitionNotAllowed`
     no serviço (já existente) e agora também a posse do lock.
6. **Fila médica**: `expire_stale_locks_for_statuses([AWAITING_DOCTOR])`
   antes da query; `select_related("locked_by")`; cada card da aba aguardando
   usa `compute_lock_display`:
   - sem reserva → [Avaliar] (primário) → `case_decide`;
   - reservado pelo próprio → [▶ Continuar avaliação] (sucesso) → `case_decide`
     (re-claim renova);
   - reservado por outro → [🔒 Reservado] desabilitado + «por {nome}».

## D3 — Heartbeat, «Sair sem decidir» e submit guard

- **Endpoints JSON** (padrão ATS): `doctor:lock_renew` e `doctor:lock_release`
   (`POST <case_id>/lock/renew|release/`), exigem `lock_token`, retornam
   `{"success": bool, ...}`; papel ativo `doctor`/`admin` + guard de acesso.
- **`static/js/work_lock.js`** adaptado do ATS (vanilla, sem dependências):
  - config via `<div id="work-lock-config" data-work-lock-config
    data-renew-url data-release-url data-lock-token data-heartbeat-ms="60000"
    data-grace-ms="240000" style="display:none;">` + `#work-lock-warning`
    (alert oculto);
  - atividade: `mousemove/mousedown/keydown/scroll/touchstart/focus`;
  - heartbeat 60s: renova só se houve atividade nos últimos 4min (grace);
    falha → aviso visível + desabilita o submit;
  - **submit guard**: submit de formulário contendo o `lock_token` marca flag
    que suprime o release (evita corrida pagehide × POST);
  - release best-effort (`keepalive`) em `pagehide` e clique em link que
    navega para fora (âncora/nova aba ignoradas).
- **«Sair sem decidir» server-side** (garantia sem JS): `<form method="post"
  action="{% url 'doctor:case_leave' case_id %}">` com `lock_token` → view
  `case_leave` faz `release_case_lock` + redirect à fila com flash
  informativo. O link do slice 001 vira este mini-form (botão outline).
  Token ausente/inválido → apenas redirect (idempotente, sem erro).
- `base.html`: `<meta name="csrf-token">` para o `fetch` do heartbeat.

## D4 — Agendador espelhado

- `scheduler:case_confirm` e `scheduler:case_deny` (GET) fazem claim com
  `context="scheduler_confirm"`, `role="scheduler"` após os guards; bloqueado
  por outro portador → warning + redirect à fila do agendador.
- POSTs de confirmação/negação exigem `lock_token` (mesma semântica de
  `lock_error` do médico) e fazem release determinístico após o sucesso.
- Fila do agendador: expiração lazy por
  `[SCHEDULER_REQUESTED, AWAITING_SCHEDULING]`, `select_related("locked_by")`,
  cards 🔒 Reservado/▶ Continuar/ação livre («Confirmar»).
- Páginas de confirmação/negação ganham o `work-lock-config` + JS (lease
  300s default — tarefa curta, igual ATS).

## D5 — Interação com workers e eventos

- Workers (`intake`, `anonymization`, `pipeline`) claimam com contexto
  `worker` e status de processamento; médico/agendador só claimam em status
  de decisão (`AWAITING_DOCTOR` / `SCHEDULER_REQUESTED|AWAITING_SCHEDULING`)
  onde não há worker ativo — sem sobreposição prática de contexto no mesmo
  status.
- Trilha: o HMD mantém os eventos `CASE_LOCK_CLAIMED/RENEWED/RELEASED/
  EXPIRED` (o ATS usa `WORK_LOCK_*`; não renomeamos — a trilha do HMD já é
  consumida pelo painel com esses rótulos).
- CSRF dos endpoints via meta (D3). Sem migration (campos existem).

## Riscos e decisões de risco

- **Testes existentes**: apenas `apps/doctor/tests/test_decision.py` submete a
  view `case_decide` — o slice 002 o atualiza para claim+token; os demais
  fluxos usam serviços diretamente (sem quebra). No scheduler, os testes do
  próprio app são atualizados no slice 004.
- **Sem JS** (navegador mínimo): heartbeat não roda; a reserva expira pela
  lease (≤1h médico) e o «Sair sem decidir» server-side continua garantindo
  liberação imediata.
- **Aba esquecida aberta**: sem atividade > grace, o heartbeat para de renovar
  e a lease vence sozinha — caso volta à fila (mesma semântica do ATS em
  produção).
