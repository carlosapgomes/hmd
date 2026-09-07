# Proposal: case-core-fsm-procedures

## Why

O HMD não tem domínio de casos: tudo que existe hoje é accounts (papéis/auth). Este change cria o núcleo de domínio clínico reutilizável — a "mecânica" que todos os changes posteriores (intake 04, anonimização 05, pipeline 06, decisão 07, agendamento 08, encerramento 09) consomem: entidade `Case` com máquina de estados de 17 estados, procedimentos por caso (neutros, catálogo de 13 tipos), trilha de auditoria append-only, locks de concorrência e comunicações por caso. É a generalização do padrão comprovado do `ats-web` (ADR-0004: dimensão de exame vive exclusivamente em `CaseProcedure`; `CaseEvent` é a fonte de verdade da história) para 13 tipos de procedimento, com FSM migrada de `django-fsm` (deprecated) para **`django-fsm-2`** (MIT, API preservada — decisão do dono em 2026-09-07; ver `temp/research/viewflow-fsm.md`).

## What Changes

- **App `apps/cases`**: modelos `Case` (núcleo enxuto: identidade, status FSM, origem/agência, timestamps, campos de lock), `CaseProcedure`, `CaseEvent`, `CaseCommunicationMessage` + migrations.
- **Catálogo de procedimentos em código** (`exam_profiles.py`): 13 tipos com label, seção de critérios (S1–S8), subtipo doctor (angio/neuro/cardio/radio), suporte anestésico e tabela de thresholds por seção; seed idempotente de verificação e teste de presença dos 13.
- **FSM**: 17 estados renomeados (`NEW → PDF_EXTRACTING → ANONYMIZING → LLM_EXTRACTING → LLM_SUMMARIZING → AWAITING_DOCTOR → DOCTOR_DENIED|DOCTOR_ACCEPTED → … → CLEANED`, + `FAILED`) com transições protegidas (transição inválida rejeitada); estados são contrato fixo — changes futuros adicionam transições, não estados.
- **Serviços de procedimentos**: declaração NIR atômica, detecção e decisão por procedimento (rows neutros, constraint de unicidade caso+tipo), eventos de auditoria em cada operação.
- **Locks/lease**: claim/assert/release/renew/expire por caso com token, contexto e expiração — concorrência entre doctor/scheduler/workers.
- **Comunicações**: mensagens `user` e `system` por caso (append-only), projetadas automaticamente de eventos FSM relevantes.
- **Sem UI**: models + serviços + seed; as telas chegam com os changes de fluxo (04+).

## Capabilities

### `case-management` (nova)

- Catálogo de 13 tipos de procedimento (habilitação por existência; extensão por código).
- FSM de 17 estados com transições protegidas e caminho `FAILED`.
- Trilha de auditoria append-only por caso.
- Locks/lease de concorrência por caso.
- Comunicações operacionais por caso (user/system).
- Procedimentos por caso: declaração, detecção e decisão atômicas e auditadas.

## Impact

- Arquivos: `apps/cases/**` (models, fsm, exam_profiles, procedures, services, signals, seed, migrations, tests), `config/settings/base.py` (INSTALLED_APPS + settings de lease), `pyproject.toml` (django-fsm-2), `.env.example`, `docs/adr/ADR-0005*`.
- Migrações novas (sem dados existentes — banco ainda sem casos).
- Contrato consumido por 04–09; mudanças de estado/catálogo após este change exigem change explícito (guardrail).

## Non-goals

- Upload de PDFs/anexos, gate de regulação, telas de intake (change 04).
- Anonimização/pseudônimos (05), prompts/schemas/LLM (06), policy determinística e prior-case (06).
- Filas/presenter/decisão médica UI (07), agendamento e unidade 2 (08), encerramento/reenvio corrigido/intercorrência (09).
- Campos de decisão/agendamento no `Case` (chegam com 07/08 — sem antecipação; migrations incrementais).
- Dashboard e notificações (11).
