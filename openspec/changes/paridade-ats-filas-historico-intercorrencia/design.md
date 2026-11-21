# Design

## Context

Paridade operacional com o ats-web em quatro eixos (ver `proposal.md` — Why): sumário
completo ao agendador, superfícies de busca histórica (NIR e agendador), intercorrência
pós-encerramento (`open_post_acceptance_issue`) e abas «Hoje». Pontos de partida
relevantes do código atual:

- A anonimização é o **diferencial HMD** e não é objeto de paridade: tokens sempre na
  fronteira LLM, re-identificação só na renderização (`apps/{doctor,scheduler}/presenters.py`,
  `apps/anonymization/reidentify.py`), `structured_data` persiste só tokens.
- O que sobrevive ao `CLEANED` já cobre a busca: `patient_name`/`patient_birth_date`/
  `patient_age/gender/race`, `agency_record_number`, `origin_unit` estão fora de
  `_CLEANED_EMPTY_VALUES` (`apps/cases/closure.py:72`); rows `CaseProcedure` (com
  `doctor_disposition`/`doctor_decided_at`), eventos, comunicações e `scheduled_*` também
  sobrevivem.
- O ciclo de reabertura pré-ciência já existe: `reopen_scheduling_after_incident`
  (`apps/scheduler/services.py:246`) — atomic + `select_for_update`, transição
  `reopen_scheduling`, limpeza dos 5 campos de agendamento, `scheduling_reopen_reason`,
  comunicação ao NIR; `confirm_case_scheduling` já aceita origem `AWAITING_SCHEDULING`.
- Filas por status: `TAB_STATUSES` em `apps/doctor/views.py:108` e
  `apps/scheduler/views.py:106`.
- Notificações (`apps/accounts/notifications.py`): o marco de reabertura
  (`CASE_STATUS_AWAITING_SCHEDULING` com `reason`) hoje notifica **apenas o criador**; o
  fan-out aos agendadores existe somente no marco `CASE_STATUS_SCHEDULER_REQUESTED`.
- Decisão de agendamento: a **confirmação** carimba `scheduled_by`/`scheduled_decided_at`;
  a **negação** (`deny_case_scheduling`, `services.py:220`) persiste apenas
  `scheduling_denial_reason` — sem decisor nem timestamp. A reabertura pré-ciência zera os
  5 campos de agendamento.
- Modelo-fonte ats-web: `open_post_acceptance_issue`/`is_post_acceptance_issue_eligible`
  (`/projects/dev/ats-web/apps/cases/services.py:576+`), busca histórica
  `scheduler_historical_search` e `closed_cases_search`.

## Goals / Non-Goals

**Goals**

- G1: Sumário clínico completo re-identificado ao agendador (remoção do truncamento).
- G2: Busca histórica por nº OU nome para NIR (encerrados + intercorrências ativas,
  qualquer criador) e agendador (processados, incluindo encerrados).
- G3: Intercorrência pós-encerramento: FSM `CLEANED → AWAITING_SCHEDULING` + issue única
  + ciclo completo de re-encerramento, com notificação correta a criador E agendadores.
- G4: Abas «Decididos Hoje»/«Processados Hoje» (janela do dia, escopo do decisor),
  entregues **após** as superfícies de arquivo existirem.

**Non-Goals**

- Contexto «operational notice» do ats-web (aviso sem transição, dependente de
  `doctor_admission_flow`, inexistente no HMD). Se demandado no futuro, entra como
  comunicação + evento de ciência em change própria.
- Histórico do **médico**: o ats-web não oferece (teste explícito "no Histórico" lá);
  paridade significa que o médico perde a visão de decisões antigas ao estreitar a aba —
  **perda operacional aprovada pelo dono**, registrada aqui; reversível em change futura
  se a operação demandar.
- Afrouxar a guarda de ciência do criador: o novo encerramento do ciclo de intercorrência
  continua exigindo o CRIADOR (`acknowledge_case_receipt`); criador indisponível →
  encerramento administrativo existente (manager/admin) como rota de escape.
- Relaxar qualquer invariante da fronteira LLM; escopo por criador de «meus casos»; gate
  do `pdf-original-viewer`; guards de papel/rota/404; dashboard.
- Exclusão/ocultação de casos de negativa total da aba decididos (quirk P2 do change 09):
  com o critério «decisão do dia por decisor», esses casos passam a aparecer naturalmente
  — o P2 é resolvido por esta change.

## Decisions

### D1 — Política de visibilidade PHI in-app (decisão do dono, registrada)

O único perímetro de restrição de PHI que o HMD mantém é a **fronteira externa** (tudo que
sai ao LLM anonimizado). Visibilidade in-app entre papis legítimos não é eixo de restrição:
NIR, médico e agendador já acessam o prontuário eletrônico completo do hospital; restringir
no app o que o prontuário expõe não protege o paciente, só cria assimetria com o ats-web.
Consequências: busca por `patient_name` liberada nas superfícies históricas; escopo
não-criador aprovado para a busca de encerrados; sumário completo ao agendador (G1). O que
permanece por ter **outra** motivação (não-PHI): guards de papel/rota (autorização), escopo
criador de «meus casos» (posse), uid fora de texto visível (referência/URL), payloads de
evento/notificação sem PHI (higiene + auditoria), gate de PDF (janela de trabalho, já é
paridade), dashboard agregado.

### D2 — Elegibilidade de reabertura fail-closed com marcador persistente de disposição

Elegível: `CLEANED` + agendamento anterior confirmado na unidade 1 + **dados retidos** +
sem issue ativa. Caso minimizado (`CASE_CLOSURE_MINIMIZATION_ENABLED=true` no
encerramento) ou purgado (`purge_closed_case_data`) é **inelegível** — reabrir sem
sumário/estrutura/PDF entregaria ao agendador um caso decrépito; o dono preferiu
fail-closed e a retenção temporária do ADR-0010 (default) mantém 100% dos casos elegíveis
no piloto.

A detecção NÃO usa proxy de campos (`summary_text` vazio etc. — proxy incompleto: não
prova existência de documentos/arquivos e pode produzir falso negativo em caso retido).
Em vez disso, novo campo persistente **`clinical_data_disposed_at`** (timestamp, nulo por
default), gravado no MESMO atomic pelos dois caminhos que dispõem dados: (a) encerramento
com minimização ativa (entry point do closure), (b) purga retroativa
(`purge_closed_case_data`). Elegibilidade lê apenas a coluna. Backfill na migration do
slice 002: `CLEANED` com `summary_text` vazio ⇒ `disposed_at` preenchido — seguro porque
caso com agendamento confirmado passou obrigatoriamente pelo pipeline (sumário não-vazio
ao encerrar); casos administrativamente fechados antes do pipeline têm sumário vazio mas
também nunca tiveram agendamento (inelegíveis por outra condição). Alternativa rejeitada:
reabrir sempre com placeholders (precedente do card do painel) — contradiz a finalidade da
disposição. Registrar emenda ao ADR-0010 (a coluna vira a evidência canônica de disposição).

**Decisão do dono (2026-11-21): unidade 2 permanece inelegível** — para a unidade 2 o
sistema nunca foi dono do agendamento (a unidade comunica a Secretaria externamente;
sem data/hora/local para snapshot/limpeza), e uma regra única de intercorrência
("unidade 1, sempre") é mais clara para a operação. Re-routing de caso unidade-2
encerrado segue pelos caminhos existentes (contato unidade/Secretaria; reenvio
corrigido). Gatilho de revisão: demanda operacional recorrente de re-encaminhamento —
a extensão é trivial (soltar a condição + emenda do requirement vigente do
`scheduling`).

### D3 — Issue ativa como campo compacto + histórico nos eventos

Padrão HMD («o histórico vive nos eventos»): um conjunto mínimo de colunas para a issue
ativa (`post_schedule_issue_status` + reason/opened_by/opened_at), sem espelhar os
~12 campos nem `cycle_id` do ats-web — ciclos múltiplos são reconstruíveis pela trilha
(evento dedicado por abertura, com snapshot). A coluna de status viabiliza o guard de
elegibilidade e o queryset da busca (`Q(status=CLEANED) | Q(issue_ativa)`) sem varredura
de eventos.

**Decisão do dono (2026-11-21): motivo único, texto livre obrigatório — reuso do padrão
do desmarcar** (`SchedulerReopenForm`): sem catálogo fechado e sem campo de mensagem
separado (o par reason+message do ats-web colapsa no motivo único). O motivo entra no
payload do evento e fica visível na trilha — precedente vigente do evento de reabertura
do desmarcar (`reason` no payload) e da decisão B do change 18 (texto livre visível na
trilha). Invariantes que seguem de pé: nada vai a LLM; notificações usam texto fixo.

### D4 — Reabertura reutiliza o maquinário existente; evento com motivo e snapshot

Nova transição FSM `reopen_post_closure` (`CLEANED → AWAITING_SCHEDULING`) e serviço
`open_post_closure_issue` em `apps/cases/closure.py` (domínio do encerramento; scheduler
consome, não possui) seguindo o padrão de `reopen_scheduling_after_incident`: atomic +
`select_for_update` + revalidação de elegibilidade sob lock, limpeza dos 5 campos de
agendamento, `scheduling_reopen_reason` reutilizado, comunicação na thread (motivo
interpolado no template, como o desmarcar), evento `CASE_POST_CLOSURE_ISSUE_OPENED` com
payload contendo o motivo informado + snapshot administrativo do agendamento anterior
(unidade/data/hora/local) — mesmo precedente do evento de reabertura vigente (D3);
sem artefatos clínicos/pipeline no payload. A re-confirmação posterior é a
`confirm_case_scheduling` existente, sem mudança.

### D5 — Carimbos de decisão: dois pares independentes, backfill auditável pela trilha

O HMD não tem, no `Case`, carimbos de decisão médica nem de decisão de agendamento por
negação; as abas «Hoje» e o histórico precisam dos dois (data + decisor). Decisão: **dois
pares de campos novos**, gravados no MESMO atomic das transições correspondentes:

- **Médico**: `Case.doctor_decided_at` + `Case.doctor_decided_by` — carimbados no atomic
  da decisão médica (a row `CaseProcedure` continua sendo o fato por procedimento; teste
  anti-drift compara `Case.doctor_decided_at` com `Max(rows)`).
- **Agendamento**: `Case.scheduling_decided_at` + `Case.scheduling_decided_by` —
  carimbados por `confirm_case_scheduling` E `deny_case_scheduling` (a negação hoje não
  carimba nada — passa a carimbar os dois campos no mesmo atomic), atualizados em
  re-decisões, **nunca zerados pela reabertura** (a reabertura limpa os 5 campos do
  agendamento corrente — «appointment atual» — que continuam sendo limpos; o carimbo é o
  fato histórico «passou por decisão de agendamento», distinto por design). Reutilizar
  `scheduled_*` para isso foi rejeitado: o detail exibiria decisor sem agendamento após
  reabertura.

**Backfill auditável** (ambos os pares, via `CaseEvent` — fonte canônica de ator+timestamp):
último evento `CASE_STATUS_DOCTOR_ACCEPTED|DENIED` por caso → carimbo médico; último
`CASE_STATUS_SCHEDULING_CONFIRMED|DENIED` → carimbo de agendamento. Fail-closed: sem
evento correspondente (ou ator nulo), o campo fica nulo — o caso entra no histórico
(quando `scheduling_decided_at` existe) mas na aba «Hoje» de ninguém. Legado de negações
sem evento não existe (toda decisão grava evento), então o backfill cobre 100% dos
legados elegíveis. Alternativa rejeitada: derivar `Max(procedures)` a cada partial de 20 s
(sem decisor, e consulta repetida).

### D6 — Superfícies de busca: views novas, guards próprios, universo por carimbo

- NIR: `closed_cases_search` + `closed_case_detail` (read-only) + POST de abertura —
  queryset `Q(CLEANED) | Q(issue_ativa)`, `Q(agency_record_number__icontains) |
  Q(patient_name__icontains)`, `[:50]` após `order_by("-created_at")`. **Rota própria**
  com guarda de escopo histórico; o guard criador do detalhe atual permanece intocado.
- Agendador: `scheduler_historical_search` + detalhe em escopo histórico — universo =
  `scheduling_decided_at` preenchido (D5: inclui confirmações E negações, casos
  reabertos e re-decididos, `CLEANED` e issue ativa, qualquer decisor; **exclui**
  negativa médica encerrada — nunca teve decisão de agendamento). Sem ações de
  agendamento no detalhe histórico; PDF segue o gate vigente (`PDF_ALLOWED_STATUSES` +
  `scheduled_by` — `CLEANED` segue fora, paridade ats-web "never for historical").
- Ambas usam referência humana (nº + sufixo curto quando duplicado), estado vazio sem
  critério, e **nenhuma escrita** fora do POST de intercorrência.

### D7 — Ordenação dos slices: visibilidade/arquivo antes do estreitamento das filas

Diretiva do dono + vantagem técnica: (1) sumário completo (visibilidade, independente);
(2) FSM/serviço da intercorrência + notificações; (3) busca NIR + detalhe + abertura;
(4) histórico do agendador **com os carimbos de agendamento e backfill** (o universo do
histórico os exige — é por isso que os carimbos de agendamento moram no slice 004, antes
do 005); (5) abas «Hoje» por último (carimbos médicos + re-scope). Quando as abas
estreitarem, os casos antigos do **agendador** já têm para onde ir; os do **médico** não
terão superfície (paridade ats-web, perda aprovada — ver Non-Goals). Slices são
independemente verificáveis e reversíveis; a implantação segue a política de commits
atômicos por slice do `AGENTS.md`.

### D8 — Notificações: extensão explícita do marco de reabertura (spec `notifications` MODIFIED)

O dispatcher vigente não faz fan-out na reabertura (só o criador) — D8 anterior assumia o
contrário e foi **refutado por review** (`apps/accounts/notifications.py:91-151`). Correção
contratual: o marco de reabertura (`CASE_STATUS_AWAITING_SCHEDULING` com `reason`,
cobrindo a reabertura pré-ciência E a pós-encerramento) passa a criar **duas** notificações:
criador (preview vigente "Reconfirme os dados do caso") + fan-out dos agendadores ativos
(novo preview fixo "Caso reaberto aguardando novo agendamento") — o caso reentrou na fila
de pendentes deles. Idempotência por `unique(recipient,event)` já cobre ambos. Isso MUDA
comportamento também da reabertura pré-ciência existente (desejado: mesmo motivo
operacional) e por isso entra como delta MODIFIED da spec `notifications` + testes de
destinatários/idempotência no slice 002. Nenhum marco novo com PHI; previews são texto
fixo.

**Decisão do dono (2026-11-21) — copy fechada**: a notificação aos agendadores reutiliza
o título existente do marco ("Caso reaberto por intercorrência" — simetria: criador e
agendadores veem o mesmo título com previews distintos) e o preview canônico é
"Caso reaberto aguardando novo agendamento" (paralelo ao preview de caso novo,
diferenciado por "reaberto" + "novo"). Zero strings novas de título.

### D9 — Issue ativa x encerramento administrativo

Regra de domínio: a issue ativa é zerada em **qualquer** chegada a `CLEANED` — ciência do
novo ciclo **ou** encerramento administrativo — no mesmo atomic, com evento de auditoria
próprio registrando o fechamento da issue (e, no caso administrativo, quem a fechou). A
corrida abertura×encerramento-administrativo é resolvida pelo `select_for_update` +
revalidação (padrão já provado). Ponto de integração: `administratively_close_case`
(`apps/cases/closure.py:268`) — único toque no caminho administrativo, coberto por teste.

## Risks / Trade-offs

- [Caso minimizado precisa voltar ao fluxo (dono muda de ideia)] → reverter D2 é local
  (elegibilidade lê a coluna) + emenda ADR-0010; sem migração destrutiva.
- [Busca por nome em coluna sem índice fica lenta com volume] → `patient_name` e
  `agency_record_number` recebem `db_index` na migration do slice 002 (prefixo `icontains`
  não usa índice btree, mas o cap 50 + volume hospitalar torna aceitável; se apertar,
  pg_trgm em change própria).
- [Médico perde visão de decisões antigas ao estreitar a aba] → perda aprovada por
  paridade (Non-Goals); registrada no manual do usuário; reversível com change futura de
  histórico médico.
- [Duas fontes de verdade para decisão médica (Case x CaseProcedure)] → carimbo derivado
  no MESMO atomic da decisão (fonte permanece a row); anti-drift `Case × Max(rows)`.
- [Fan-out estendido na reabertura pré-ciência muda comportamento vigente] → deliberado
  (D8), coberto por delta de spec + testes; rollback = reverter o dispatcher.
- [Backfill encontra ator nulo/evento ambíguo] → fail-closed: campo nulo, caso fora das
  abas «Hoje» (documentado); histórico não é afetado quando `scheduling_decided_at`
  existe.
- [Dependência operacional do criador para fechar o ciclo de intercorrência] → escape
  documentado: encerramento administrativo existente (D9/Non-Goals); guarda intocada.

## Migration Plan

Três migrations aditivas, todas com defaults nulos/vazios (casos existentes válidos):

1. **Slice 002**: colunas da issue ativa (D3) + `clinical_data_disposed_at` com backfill
   (D2) + índices de busca (`patient_name`, `agency_record_number`).
2. **Slice 004**: `scheduling_decided_at`/`scheduling_decided_by` + backfill por eventos
   (D5) — exigido pelo universo do histórico antes das abas.
3. **Slice 005**: `doctor_decided_at`/`doctor_decided_by` + backfill por eventos (D5).

Deploy sem janela especial: cada migration acompanha seu slice (commit atômico por slice,
política do `AGENTS.md`); colunas aditivas não quebram código anterior. Rollback: reverter
deploy (migrations aditivas permanecem inócuas). Após qualquer migration nova em teste:
rodar `pytest --create-db` uma vez (quirk documentado do harness).

## Open Questions

Nenhuma — as três questões foram decididas pelo dono (2026-11-21): unidade 2 inelegível
(D2), copy da notificação aos agendadores (D8) e motivo único livre reusando o padrão do
desmarcar (D3).
