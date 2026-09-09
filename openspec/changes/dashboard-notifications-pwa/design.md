# Design: dashboard-notifications-pwa

Referências: plano §"apps/dashboard" e linha 116 (templates+Bootstrap+PWA
ícone `HMD`; dashboard/manual/notificações herdados/adaptados do ats-web);
respostas.md #12 (dono: nada fora do MVP; ícone `CHD`→`HMD`); padrões
ats-web (UserNotification/retention/redirect/badge, manifest/sw.js, manual)
SOMENTE-LEITURA; changes HMD 03 (comunicações/eventos/signal), 07–09
(fila médica/agendador/closure — fontes imutáveis de resultado).

## D1 — Notificações por marcos (model + signal + idempotência)

Model `UserNotification` em **apps/accounts** (padrão ats-web; FK cross-app
`Case`/`CaseEvent`): `notification_id` UUID pk, `recipient` FK User CASCADE
`related_name="notifications"`, `event` FK `CaseEvent` CASCADE (nullable —
reserva), `case` FK `cases.Case` CASCADE, `notification_type`
(`final_reply_posted|scheduler_requested|scheduling_reopened`),
`title` (≤160), `body_preview` (≤240), `created_at`, `read_at` null.
**UniqueConstraint (`recipient`, `event`)** — idempotência estrutural
(signal re-disparado não duplica); índices (`recipient`, `read_at`,
`created_at`) e (`case`, `created_at`).

**Fonte: signal `CaseEvent.post_save`** (apps/accounts/signals.py, wired no
`ready()` — padrão dos signals existentes) filtrando `created` e o conjunto
de marcos — **a notificação deriva do EVENTO, nunca da mensagem `system`**
(D6):

- `CASE_STATUS_FINAL_REPLY_POSTED` → destinatário: `case.created_by`
  (NIR). Título fixo "Resposta final disponível"; preview por
  `payload["source"]` (mapa fixo PT-BR: negativa médica / negativa de
  agendamento / agendamento confirmado) com **fallback** para source
  inesperado/ausente ("Resposta final disponível para o caso") — **sem
  PHI** (nenhum nome/nº registro).
- `CASE_STATUS_SCHEDULER_REQUESTED` → fan-out para
  `User.objects.filter(roles__name="scheduler", is_active=True,
  account_status="active").distinct()` (papel fixo do modelo Role, não papel
  de sessão; sem contas inativas — conferir valores de `account_status`
  no model); título "Caso pronto para agendamento".
- `CASE_STATUS_AWAITING_SCHEDULING` **com `"reason" in payload`** (é assim
  que a reabertura por intercorrência do 08 se marca — `reopen_scheduling`
  carrega `extra_payload={"reason": ...}`) → `case.created_by`; título
  "Caso reaberto por intercorrência".

Criação em `apps/accounts/notifications.py::create_milestone_notifications(
event)` (serviço puro, testável): `get_or_create` por (recipient, event) —
idempotente por construção; falha individual NUNCA derruba a transição do
caso (signal envolto em try/except com log — notificação é suplementar).
Eventos de fechamento/limpeza NÃO notificam (conjunto fechado acima).

## D2 — Badge, lista, leitura e redirect

- **Badge**: context processor `apps/accounts/context_processors.py::
  notification_unread_count` (adicionar em `TEMPLATES` settings) expõe a
  contagem de não lidas em toda página; `base.html` ganha o sino com
  contagem (padrão visual do ats-web) linkando a lista (`{% url
  "notifications" %}`).
  Endpoint JSON `notifications_unread_count` (mesma contagem) para
  uso futuro/PWA — **sem polling por padrão** (atualiza a cada navegação).
- **Lista** (`notifications`): `visible_for_list()` — não lidas OU lidas
  dentro de `NOTIFICATION_READ_RETENTION_HOURS` (default 48, env;
  nada é apagado, leitura antiga só sai da lista). Ordena `-created_at`.
- **Abrir** (`notifications_open`, **POST**; nomes de rota GLOBAIS — ver
  nota de rotas abaixo): marca `read_at` (só do dono —
  `get_object_or_404(recipient=request.user)`) e redireciona via
  `resolve_notification_redirect_url(case, active_role)`: nir→
  `intake:case_detail`, doctor→`doctor:case_detail`, scheduler→
  `scheduler:case_detail` (rotas existentes fazem o guard de acesso real;
  sem papel/admin→ `home`). Sem papel ativo → home.
- **Marcar todas** (`notifications_mark_all_read`, POST): `read_at
  = now` nas não lidas do usuário.

**Nota de rotas (P1 review)**: `apps.accounts.urls` é incluído na raiz **sem
`app_name`** e seus nomes (`home`, `login`, `profile`, `switch_role`,
`logout`) são globais, referenciados sem namespace em dezenas de lugares.
**Decisão A (minimal)**: as rotas novas (`notifications`,
`notifications_open`, `notifications_mark_all_read`,
`notifications_unread_count`, `manual`) entram em `apps/accounts/urls.py`
**sem namespace** e são referenciadas pelos nomes globais (`reverse(
"notifications")`, `{% url "notifications" %}` etc.). NÃO introduzir
namespace de accounts neste change.

## D3 — Dashboard gerencial (apps/dashboard)

App novo `apps/dashboard` com **services puros em `apps/dashboard/metrics.py`
(funções recebem queryset/período — testáveis sem request)** e uma view
`dashboard:home` (GET `?period=hoje|7d|30d|tudo`, default hoje). Acesso:
**qualquer usuário autenticado** (contagens agregadas, zero-PHI; intranet —
decisão de produto documentada, gate por papel fica trivial depois). Link
"Painel" na navbar (sempre visível autenticado).

Fontes **imutáveis** (lição do ats-web: nada depende do status FSM
transitório sozinho; **emenda P1 review**): a população é sempre **casos
com `created_at` no período**; o resultado por caso (`outcome`) é o
`payload["source"]` do **ÚLTIMO** evento `CASE_STATUS_FINAL_REPLY_POSTED`
do caso, **apenas quando o status atual é pós-final** (`FINAL_REPLY_POSTED`,
`AWAITING_NIR_ACK`, `CLEANING`, `CLEANED`); caso cujo status voltou ao
pipeline (reaberto por intercorrência, aguardando nova confirmação) é **em
andamento** — isso elimina dupla contagem e `em_andamento` negativo quando
o caso tem 2 eventos finais com sources distintos (reabertura). Tipo/
decisão = `CaseProcedure` (campo de decisão é **`doctor_disposition`**
`approved|denied|pending`; `doctor_decided_at` é **por row** de
procedimento — `Case` NÃO tem `doctor_decided_at`); unidade =
`scheduled_unit` atual dos casos com outcome agendado (reaberto em voo tem
`None` e conta como em andamento). A tabela por tipo conta **rows de
procedimento**, não casos (somatório ≠ total do resumo — esperado;
notar no template).

Métricas do período (base `created_at` do caso):
- **Resumo**: total (população); agendados (outcome `SCHEDULING_CONFIRMED`);
  negados (outcome `DOCTOR_DENIED` ∪ `SCHEDULING_DENIED`); em andamento
  (total−agendados−negados — por construção ≥ 0 com a regra de outcome);
  encerrados (`status=CLEANED` dentro da população).
- **Por tipo**: para cada `procedure_type` do catálogo (ordem canônica):
  total de rows, aprovados, negados, sem decisão (`doctor_disposition`).
- **Por unidade**: agendados unidade 1 / unidade 2 (por `scheduled_unit`
  dos casos com outcome agendado) e negados de agendamento como linha
  informativa.
- **Tempo médio até decisão médica**: casos da população com ≥1 row
  decidida (`doctor_disposition ≠ pending`) e `doctor_decided_at` no
  período: média de `max(doctor_decided_at por caso) − case.created_at`,
  humanizada (ex. "3h 42m"); sem decididos → "—".

Template Bootstrap com cards da tabela de tipos + resumo em destaque; zero
dados de paciente (nenhum nome/nº registro/idade — só labels de
tipo/unidade e números).

## D4 — PWA (manifest + SW + ícones HMD)

- `static/manifest.json`: name "Regulação - HMD", short_name "HMD",
  start_url "/", display standalone, theme/background do app, ícones.
- `static/icons/`: SVG base+maskable com as letras **HMD** (hand-written) e
  PNGs 72–512 (+maskable 192/512) **gerados por `scripts/
  generate_pwa_icons.py`** usando PyMuPDF (dependência existente — desenha
  fundo na cor tema do app + texto HMD centralizado; maskable com safe
  zone) — script commitado + PNGs commitados (reprodutível).
- `static/js/sw.js` adaptado do ats-web: pre-cache de estáticos
  (manifest/css/ícones), activate limpando caches antigos, fetch GET-only —
  network-first para `/static/` e para navegação HTML **com bypass das
  rotas de PDF do caso** (`url.pathname` contém `/pdf/` — viewer nativo não
  renderiza via respondWith), network-only para o resto; POST/PUT/DELETE
  nunca interceptados.
- `base.html`: `<link rel="manifest">`, meta theme-color, e snippet inline
  de registro do SW (`if ('serviceWorker' in navigator)` no load,
  `/static/js/sw.js`).

## D5 — Manual de usuário

Rota `manual` (nome GLOBAL, sem namespace — mesma decisão A de D2) +
`templates/accounts/manual.html` (link navbar,
`target="_blank"`): visão geral do ciclo (NEW→CLEANED em texto), seções por
papel — NIR (enviar relatório+anexos, acompanhar, ciência, reenvio
corrigido), médico (fila, detalhe com cards de anexos/verificação, decidir),
agendador (fila, confirmar/negar por unidade, intercorrência unidade 1),
transversais (notificações, painel, instalar o app/PWA). HTML estático
imprimível (PDF fica como follow-up fora do escopo).

## D6 — Esclarecimento da spec case-management (MODIFIED)

O requisito "Comunicações operacionais por caso" diz hoje "**Mensagens
`system` não geram notificação nem estado de leitura**". O comportamento do
03 não muda (mensagens continuam projeção pura, sem estado de leitura), mas
com este change eventos da FSM passam a gerar `UserNotification` — o delta
MODIFIED esclarece a fronteira: notificações derivam de **eventos**, não de
mensagens; a thread de comunicações permanece sem estado de leitura. Todos
os cenários originais preservados + 1 novo (evento gera notificação sem
tocar a mensagem `system`).
