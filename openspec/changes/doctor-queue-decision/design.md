# Design: doctor-queue-decision

Decisões técnicas do change 07. Referências: plano §10 (decisão médica) e
§5 (subtipos); ats-web `apps/doctor/` (somente-leitura — padrões de
queue/decision/presenter); changes 03/06 (serviços e artefatos já entregues).

## D1 — Subtipos de médico: model M2M semeado do catálogo

Plano §5: "`specialties` M2M (`neuro|cardio|angio|radio`; vazio =
generalista)". Implementação code-first coerente com `Role`: model
`DoctorSpecialty(name unique)` em `apps/accounts`, semeado por **data
migration** a partir de `apps.cases.procedure_catalog.VALID_DOCTOR_SUBTYPES`
(fonte única — o catálogo do change 03 também define `doctor_subtipo` por
tipo). `User.specialties = M2M(DoctorSpecialty, blank=True)`; conjunto vazio =
generalista (vê qualquer subtipo). A atribuição é feita no Django admin
(sem UI self-service — fora de escopo). Helper puro
`user_doctor_subtypes(user) -> set[str]` alimenta o access control sem
duplicar a regra.

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
| `nir`, `scheduler`, `manager`, sem papel ativo | — (403) | — |

Como todos os 13 tipos têm `doctor_subtipo`, todo caso tem ao menos um
subtipo — a cobertura operacional de um subtipo sem médico dedicado cai para
generalistas/admin (plano §2, nota de habilitação). Implementado por guard de
view (`role_required("doctor", "admin")` + `can_access_case(user, case)`) e
**reforçado no POST de decisão** (nunca só no template). Manager fica de fora
da fila: perfil de gestão, dashboard no change 11.

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

## D4 — Sem lock de posse do médico (divergência vs ats-web)

O ats-web renova/libera lock de posse por caso (`doctor_lock_renew/release`).
No HMD a decisão é um **POST único atômico** sobre o serviço do change 03
(`select_for_update` + transição FSM): segundo submit concorrente falha com
`TransitionNotAllowed` (caso já saiu de `AWAITING_DOCTOR`) e a view traduz
para mensagem de estado + redirect ao detalhe — sem estado de posse, sem
renovação, sem leak. Se a fricção real aparecer em produção, o mecanismo entra
como change próprio (lock de UI ≠ lock de domínio).

## D5 — Fila por lifecycle com filtro de subtipo

Fila única em `/doctor/` (paginada, FIFO por `created_at`): filtro de estado
(`aguardando` = `AWAITING_DOCTOR`, default; `decididos` = estados pós-decisão
`DOCTOR_DENIED|DOCTOR_ACCEPTED|SCHEDULER_REQUESTED`) **e** dropdown de subtipo
(`Todas` + subtipos **do usuário** — generalista sem subtipo vê `Todas` +
qualquer subtipo; admin idem). O filtro por subtipo usa o mesmo predicado de
D2 (consistência fila×detalhe). Badge por tipo com o subtipo visível e
contagem de pendentes por subtipo no cabeçalho (contexto operacional).

## D6 — Decisão por procedimento: formulário sobre serviço existente

`DoctorDecisionForm` dinâmico (padrão ats-web `procedure_<type>__disposition`
/ `procedure_<type>__reason`): um campo de escolha `approved|denied` + motivo
por procedimento **declarado**; motivo **obrigatório** quando `denied`
(validação no form; o serviço do change 03 só exige presença de texto).
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
