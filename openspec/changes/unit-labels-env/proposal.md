# Change: unit-labels-env

## Why

O piloto de produção revelou que as unidades têm **nomes reais** distintos de
"Unidade 1"/"Unidade 2", mas esses labels estão hardcoded em ~15 pontos de
exibição (form do agendador, resposta final ao NIR, painel, detail do caso,
manual). Não existem env vars para configurá-los (decisão do dono, 2026-09-13).

## What Changes

- Novas envs `HMD_UNIT_1_LABEL`/`HMD_UNIT_2_LABEL` (defaults "Unidade 1"/
  "Unidade 2"), documentadas em `.env.example` e repassadas pelo compose de
  produção.
- Helper central `apps/cases/units.py` (`unit_labels()`/`unit_label(value)`) —
  fonte única dos labels; hoje existem **duas** tabelas duplicadas
  (`_UNIT_LABELS = dict(SchedulingUnit.choices)` em `scheduler/views.py` e
  `scheduler/presenters.py`) mais o dict do dashboard, todas derivando do
  modelo.
- Todos os pontos de EXIBIÇÃO passam pelo helper: choices do form, resposta
  final da unidade 2 (constante → texto interpolado com o label), scheduler
  views/presenters, dashboard, manual e help texts de template.
- `SchedulingUnit.IntegerChoices` fica **intocado** (labels canônicos;
  migration 0008 congela o histórico; validação continua `unit ∈ {1, 2}`).
- Spec `scheduling` MODIFIED (texto "exatamente" da resposta final passa a
  citar o label configurado) + ADDED (requisito de rótulos configuráveis).
- Sem migration, sem mudança de FSM/fluxo; textos já gravados em eventos
  seguem imutáveis (append-only — o label vigente é aplicado no momento da
  postagem).

## Impact

- **Specs**: `scheduling` (1 MODIFIED + 1 ADDED).
- **Código**: `config/settings/base.py`, `apps/cases/units.py` (novo),
  `apps/scheduler/{services,forms,views,presenters}.py`,
  `apps/dashboard/views.py`, `apps/accounts/context_processors.py`,
  templates (`scheduler/case_detail.html`, `accounts/manual.html`),
  `.env.example`, `docker-compose.prod.yml`.
- **Testes**: novos testes de label configurável (services/forms/dashboard/
  manual) + os 8 pontos de teste existentes que citam o texto seguem verdes
  com os defaults.
- **Risco**: baixo — substituição mecânica de exibição; dado persistido não
  muda (o `scheduled_unit` continua 1/2).
