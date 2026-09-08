# Design: doctor-queue-decision

Decisões técnicas do change 07. Referências: plano §10 (decisão médica) e
§5 (subtipos); ats-web `apps/doctor/` (somente-leitura — padrões de
queue/decision/presenter); changes 03/06 (serviços e artefatos já entregues).

## D1 — Subtipos de médico: model M2M semeado do catálogo

Plano §5: "`specialties` M2M (`neuro|cardio|angio|radio`; vazio =
generalista)". Implementação code-first coerente com `Role`: model
`DoctorSpecialty(name unique)` em `apps/accounts`, M2M
`User.specialties = M2M(DoctorSpecialty, blank=True)`; conjunto vazio =
generalista (vê qualquer subtipo). A atribuição é feita no Django admin
(sem UI self-service — fora de escopo). Helper puro
`user_doctor_subtypes(user) -> set[str]` alimenta o access control sem
duplicar a regra.

O seed dos 4 subtipos vive na **data migration da própria migration**
(`0003_doctor_specialty`, próxima na sequência de `apps/accounts`) com os
nomes **escritos na migration** (histórico congelado, idêntico em banco novo
e incremental — migrations não devem importar código vivo); a fonte única do
catálogo é garantida por **teste de invariante** (DB ==
`VALID_DOCTOR_SUBTYPES`), não por import na migration. Nota: o seed de
`Role` segue outro caminho (management command `seed_admin`) — specialties
usam data migration porque já nascem com o model, sem comando de
provisionamento.

Alternativa rejeitada: `ArrayField`/`CharField` multi-valor — perde integridade
referencial e o padrão M2M já estabelecido por `roles`.

## D2 — Access control por subtipo (matriz fechada)

O roteamento usa **apenas o conjunto declarado** do caso
(`get_declared_procedure_types`) × subtipos do médico × papel ativo:

| Papel ativo | Ver fila/detalhe | Decidir |
| --- | --- | --- |
| `doctor` generalista (sem subtipo) | todos os casos | sim |
| `doctor` com subtipos S | casos com ≥1 tipo declarado cujo `doctor_subtipo ∈ S` | sim |
| `admin` | todos | sim |
| `nir`, `scheduler`, `manager`, sem papel ativo | 403 | — |

(Usuário **anônimo** → redirect ao login, 302 — semântica do `role_required`
existente; 403 é para sessão autenticada com papel ativo não autorizado.)

Como todos os 13 tipos têm `doctor_subtipo`, todo caso tem ao menos um
subtipo — a cobertura operacional de um subtipo sem médico dedicado cai para
generalistas/admin (plano §2, nota de habilitação). Implementado por guard de
view (`role_required("doctor", "admin")` + `apps/doctor/access.py::
can_access_case(user, case)`) e **reforçado no POST de decisão** (nunca só no
template). Manager fica de fora da fila: perfil de gestão, dashboard no
change 11 — **decisão pendente de validação do dono** (o plano §6 cita
manager na re-identificação, mas o §10 não o menciona na fila).

## D3 — Presenter re-identificado como serviço puro + helper recursivo

O presenter (`apps/doctor/presenters.py`) monta um dict de contexto a partir
dos artefatos persistidos; o template apenas renderiza. A re-identificação de
strings existe (`apps/anonymization/reidentify.py::reidentify/reidentify_text`),
mas `structured_data`/`policy_result`/`suggested_action` são JSON aninhados —
este change adiciona **`reidentify_structure(value, pseudonym_map)`** recursivo
(dict/list/str; outros tipos atravessam) no módulo existente (mudança
**aditiva**, com testes no app anonymization; reuso previsto no change 10 para
anexos). O presenter aplica `reidentify_structure` aos artefatos e
`reidentify_text` ao `summary_text`. A barreira de privacidade não muda: LLM
nunca vê o resultado da re-identificação (nenhuma chamada LLM neste change); a
re-identificação ocorre apenas na renderização para `doctor`/`admin`.

Prior-case: o card usa `lookup_prior_case_context` (read-only, sem evento —
eventos de lookup já são gravados pelo pipeline do change 06). O motivo no
`PriorCaseSummary` chega **anonimizado** (para o LLM2); o card do médico busca
o **motivo real** na row do caso prévio via `prior_case_id` (médico vê dados
reais).

Tokens remanescentes no `summary_text`: o motivo prior-case é anonimizado
por uma chamada própria (`_anonymized_reason`), cujos tokens vivem em
**espaço distinto** do mapa do caso. A re-identificação usa o mapa do caso
(prior = mesmo paciente, então `<PESSOA_1>` converge); tokens de outros
tipos que colidam podem sobrar **como tokens** — sem vazamento (token é
anônimo por definição). O assert de aceite do slice 003 é escopado: nenhum
token **do mapa do caso** sobrevive à renderização.

## D4 — Sem lock de posse do médico (divergência vs ats-web)

O ats-web renova/libera lock de posse por caso (`doctor_lock_renew/release`).
No HMD a decisão é um **POST único atômico** sobre o serviço do change 03
(`select_for_update` + transição FSM): segundo submit concorrente falha com
`TransitionNotAllowed` (caso já saiu de `AWAITING_DOCTOR`) e a view traduz
para mensagem de estado + redirect ao detalhe — sem estado de posse, sem
renovação, sem leak. Se a fricção real aparecer em produção, o mecanismo entra
como change próprio (lock de UI ≠ lock de domínio).

## D5 — Fila por lifecycle com filtro de subtipo

Fila única em `/doctor/` (FIFO por `created_at`): filtro de estado
(`aguardando` = `AWAITING_DOCTOR`, default; `decididos` = estados pós-decisão
— na prática `DOCTOR_DENIED|SCHEDULER_REQUESTED`, pois `DOCTOR_ACCEPTED` é
transitório no mesmo atomic do encadeamento; incluí-lo no filtro é inofensivo
e cobre casos de borda) **e** dropdown de subtipo (`Todas` + subtipos **do
usuário** — generalista sem subtipo vê `Todas` + qualquer subtipo; admin
idem). O filtro por subtipo usa o mesmo predicado de D2 (consistência
fila×detalhe). Badge por tipo com o subtipo visível e contagem de pendentes
por subtipo no cabeçalho (contexto operacional). É a **primeira view
paginada do projeto** — Django `Paginator` (não há padrão anterior; my_cases
não pagina).

## D6 — Decisão por procedimento: formulário sobre serviço existente

`DoctorDecisionForm` dinâmico (padrão ats-web — lá os campos são
`procedure_<type>`/`procedure_<type>_reason`; o HMD é livre nos nomes desde
que dinâmicos por procedimento declarado): um campo de escolha
`approved|denied` + motivo por procedimento **declarado**; motivo
**obrigatório** quando `denied` (validação no form; o serviço do change 03
só exige presença de texto).
Submit → `record_doctor_procedure_decisions` (rows + evento
`CASE_DOCTOR_DECISIONS_RECORDED` + transição `DOCTOR_DENIED` ou
`DOCTOR_ACCEPTED → SCHEDULER_REQUESTED` no mesmo atomic). O caso decidido
fica acessível na aba "decididos" (detalhe read-only com decisões por
procedimento, motivo, ator e trilha). Nada de `post_final_reply` aqui (change
09) — negado permanece `DOCTOR_DENIED` com a decisão auditável.

## D7 — Servir o PDF original

Link "PDF original" no detalhe (doctor/admin): view serve `CaseDocument.file`
(`FileResponse`, streaming, `position` como ordem de leitura), 404 sem
documento. O PDF contém dados reais — permitido para `doctor`/`admin`
(D2); `nir` já o vê pelo intake (change 04).

## D8 — Migrações e eventos

Única migration nova: `accounts` (model `DoctorSpecialty` + M2M + seed por
data migration). Nenhum evento novo: reusa `CASE_STATUS_*` (transições) e
`CASE_DOCTOR_DECISIONS_RECORDED` (change 03). Nenhum campo novo em
`Case`/`CaseProcedure` — a autoria da decisão vive no `actor` do evento.

## D9 — Escopo do presenter e o "inclui?" do plano §10

O plano §10 menciona "decisão (aceita/nega/inclui?)" — herança do ats-web v2.
No HMD o terceiro desfecho **não se aplica**: divergência de tipos é gate NIR
do change 06 (retenção + bypass), e a decisão médica é **binária por
procedimento declarado** (`approved|denied`). Decisão registrada (validada
pelo dono junto com a revisão deste plano).

Presenter exibe as seções do artefato LLM1 previstas no schema base
(`apps/pipeline/schemas/base.py`): pedido, `contexto_clinico`,
`linha_do_tempo`, `exames`, `medicações`, `comorbidades`,
`contraindicações`, `trechos_nao_classificados` — além dos alertas da policy
com **requisitos gerais acionáveis** (protocolos de suspensão/
dessensibilização/nefroproteção conforme os alertas de cada caso),
recomendação por procedimento, agregado, prior-case e documentos.
