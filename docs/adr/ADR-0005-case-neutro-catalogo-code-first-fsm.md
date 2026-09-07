# ADR-0005: Case neutro + catálogo code-first + django-fsm-2 (MIT)

## Status

Accepted

## Contexto

O HMD regula 13 tipos de procedimento de hemodinâmica (angio/neuro/cardio/
radio). O núcleo de domínio (change 03) precisa de: (a) a dimensão de
procedimento neutra em relação ao caso — a lição do projeto-fonte ats-web
(`cases.0016`, `Case.exam_type` removido) é que a dimensão de exame vive em
rows `CaseProcedure` com unicidade `(case, procedure_type)`, nunca em coluna
do `Case`; (b) um catálogo dos 13 tipos com perfis e thresholds clínicos
(S1–S8), consumido por políticas e prompts; (c) uma máquina de estados de
17 estados com transições protegidas e trilha de auditoria append-only. A
pesquisa `temp/research/viewflow-fsm.md` comparou `viewflow.fsm` (via
`django-viewflow`) e `django-fsm-2`, ambos compatíveis com Django 5.2/Py 3.13.

## Decisão

- **Case neutro (padrão do ADR-0004 do ats-web, D2)**: a dimensão de
  procedimento vive exclusivamente em rows `CaseProcedure` (1–N por caso,
  `UniqueConstraint(case, procedure_type)`, decisão/declaração/detecção por
  row). O `Case` do slice 002 fica enxuto (D7): sem campos de procedimento,
  pdf, LLM, decisão ou agendamento — chegam em changes próprios.
- **Catálogo code-first (`apps/cases/procedure_catalog.py`, D3)**: os 13
  perfis e as seções S1–S8 vivem em código versionado (dataclasses
  congeladas + registro ordenado), com lookup fail-fast. Novos tipos = mudança
  de código em change explícito, sem migração de dados; o comando
  `seed_procedure_catalog` é verificação idempotente.
- **FSM via `django-fsm-2==4.2.4` (D1, confirmada pelo dono em 2026-09-07)**:
  MIT, drop-in da API django-fsm (`@transition`/`FSMField(protected=True)`/
  signals), mantendo as referências de código do ats-web válidas ~1:1. Estados
  renomeados conforme D4; o estado `R2_POST_WIDGET` do ats-web é extinto
  (17 estados).

## Alternativas Consideradas

1. **`django-viewflow` (viewflow.fsm)** — rejeitada: licença **AGPLv3+**
   exige validação institucional; a API (flow class + hooks `on_success`, sem
   signals Django) diverge do padrão clonado do ats-web, invalidando as
   referências de código.
2. **Catálogo em banco** (tabela de tipos com edição por UI) — rejeitada:
   sem requisito de edição por UI no MVP; banco criaria segunda fonte de
   verdade para o mesmo contrato clínico (thresholds/labels/prompts).
3. **FSM manual** (TextChoices + serviços + guards) — rejeitada: custo de
   reimplementar validação/erros/signals sem ganho.

## Consequências

- **17 estados são contrato** (D4): changes futuros adicionam transições,
  nunca estados; cada transição é testada individualmente (slice 002).
- **Divergências deliberadas vs ats-web** (D5): `CaseEvent` ganha
  `actor_role` (papel ativo importa no HMD multi-role) e usa
  `actor_type ∈ {user, system}`; gravação direta — cada transição faz
  `save()` e cria o evento no mesmo `transaction.atomic()`, sem o padrão
  pending-event + signal `Case.post_save`.
- **Catálogo como código** deriva de documento clínico (`parametrosHMD.md`):
  teste de coerência (13/13, seções, subtipos) trava regressão; mudança
  clínica = change explícito.
- **django-fsm-2 é MIT e mantido** (django-commons): versão pinada
  (`4.2.4`); a superfície usada é pequena (`@transition`/`FSMField`/
  `TransitionNotAllowed`).
- Positivo: sem decisão de licença pendente; padrão do ats-web preservado
  para slices e changes futuros.
