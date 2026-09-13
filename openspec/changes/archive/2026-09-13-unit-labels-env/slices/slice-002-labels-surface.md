# Slice 002 — Superfície de exibição: views, dashboard, context processor e templates

## Objetivo

Todos os pontos de EXIBIÇÃO de labels de unidade passam pela fonte única do
slice 001 (`apps/cases/units.py`): as tabelas duplicadas de
`scheduler/views.py`/`presenters.py`, o dict inline do dashboard, o manual do
usuário e o help text de intercorrência do detail do agendador — este último
e o manual via context processor global `unit_labels`.

## Contexto necessário (ler antes de editar)

- `apps/cases/units.py` — helper do slice 001 (já mergeado).
- `apps/scheduler/views.py:112` e `apps/scheduler/presenters.py:32` —
  `_UNIT_LABELS: dict[int, str] = dict(SchedulingUnit.choices)` (duplicatas a
  eliminar) e seus usos (`unit_label = _UNIT_LABELS.get(...)` em views:151 e
  presenters:99).
- `apps/dashboard/views.py:54-62` — dict inline `"unit_labels"` no context do
  painel (comenta "derivados do modelo"; passa a derivar do helper).
- `apps/intake/views.py:86` — TERCEIRA tabela duplicada
  `_UNIT_LABELS = dict(SchedulingUnit.choices)` (consumida em :185; o
  `templates/intake/case_detail.html:139` renderiza `scheduling.unit_label`)
  — inclusão emendada após review do slice 001 (a spec ADDED exige TODOS os
  pontos de exibição, incl. detail do NIR).
- `apps/accounts/context_processors.py` — padrão dos processors existentes
  (`notification_unread_count`); registrar o novo em
  `config/settings/base.py` (TEMPLATES context_processors).
- `templates/accounts/manual.html:179-199` — ocorrências literais de
  "Unidade 1"/"Unidade 2" (6 pontos, incluindo a citação da resposta fixa).
- `templates/scheduler/case_detail.html:190-195` — help text da
  intercorrência ("confirmado na Unidade 2, que comunicará a Secretaria").
- `apps/accounts/tests/test_manual.py:43-44,150-151` — asserções com os
  defaults (devem continuar passando SEM edição).
- `apps/dashboard/tests/test_views.py:133` — idem ("Unidade 1" in content).
- `openspec/changes/unit-labels-env/design.md` — D4 (context processor).

## Requisitos verificáveis

- **R1** — `scheduler/views.py`, `scheduler/presenters.py` e
  `intake/views.py` eliminam as tabelas duplicadas e usam
  `unit_label(...)`/`unit_labels()` do helper; o detail do agendador E o
  detail do NIR exibem o label configurado.
- **R2** — `dashboard/views.py` compõe `unit_labels` do helper (chaves
  `unit_1`/`unit_2` preservadas — o template consome `unit_labels.unit_1`).
- **R3** — context processor global `unit_labels` em
  `apps/accounts/context_processors.py` registrado no `base.py`, expondo
  `unit_labels.unit_1`/`unit_labels.unit_2` a todos os templates.
- **R4** — `templates/accounts/manual.html` e o help text de intercorrência
  em `templates/scheduler/case_detail.html` usam as variáveis do processor
  (nenhum label literal de unidade permanece nesses templates — grep final).
- **R5** — **Testes novos** (ex.: `apps/dashboard/tests/test_unit_labels.py`
  ou no arquivo de testes do template correspondente): com
  `override_settings(UNIT_LABELS={1: "Hemodinâmica HGRS", 2: "Unidade Satélite"})`,
  o painel e o manual (e o detail do agendador quando aplicável) exibem os
  labels configurados; sem override, testes existentes seguem verdes sem
  edição (defaults).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/scheduler/views.py`, `apps/scheduler/presenters.py`, `apps/intake/views.py` | `rg -n "_UNIT_LABELS" apps/scheduler` → 0 matches; teste existente do detail com default segue verde |
| R2 | `apps/dashboard/views.py` | `test_views.py::test_dashboard_*` existentes (default) + novo com override citando o label no content |
| R3 | `apps/accounts/context_processors.py`, `config/settings/base.py` | `rg -n "unit_labels" config/settings/base.py` → registro no TEMPLATES; teste novo: response do manual contém label com override |
| R4 | `templates/accounts/manual.html`, `templates/scheduler/case_detail.html` | `rg -n "Unidade 1\|Unidade 2" templates/accounts/manual.html templates/scheduler/case_detail.html` → 0 matches literais (exceto dentro de comentários, se houver) |
| R5 | testes novos (dashboard/accounts) | `TEST_DB_PORT=55435 uv run pytest apps/dashboard apps/accounts/tests/test_manual.py -q` com os novos |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/intake/views.py
  - apps/scheduler/views.py
  - apps/scheduler/presenters.py
  - apps/dashboard/views.py
  - apps/accounts/context_processors.py
  - config/settings/base.py        # apenas o registro do context processor
  - templates/accounts/manual.html
  - templates/scheduler/case_detail.html
  - apps/dashboard/tests/test_views.py   # OU arquivo novo de test_labels — escolha um, mantenha o padrão do app
  - apps/accounts/tests/test_manual.py   # apenas se ADICIONAR teste de override (não editar as asserções existentes)

allowed_incidental_files: []

out_of_scope:
  - apps/scheduler/services.py e forms.py (slice 001, encerrado)
  - apps/cases/models.py e migrations
  - outros templates que não os listados (intake/case_detail.html mostra o label via dado do caso — se ele renderiza label de unidade por conta própria, PARE e reporte)
  - README, .env.example, compose (slice 001)
```

Escalamento: se `templates/intake/case_detail.html` ou qualquer outro
template renderizar label de unidade próprio (não vindo do processor/da view),
ou se algum teste além dos previstos depender de label literal, **pare e
reporte**.

## Notas de implementação

- No manual, a citação da resposta fixa (hoje entre `<em>`) concatena o label
  configurado — cuide da pontuação em volta ("na {{ unit_labels.unit_2 }},
  que comunicará a Secretaria") mantendo o sentido do texto.
- O processor lê `settings.UNIT_LABELS` por requisição (sem cache de módulo).
- Não crie abstração além do que existe; substituição direta.

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/dashboard apps/accounts/tests/test_manual.py -q`
- falha esperada: os testes NOVOS de label configurado falham (painel/manual
  mostram "Unidade 1"/"Unidade 2" derivados do modelo — não do
  `settings.UNIT_LABELS`).

### GREEN

- mesmo comando — 0 failed (novos + existentes).

### Verificação do slice

- `TEST_DB_PORT=55435 uv run pytest apps/scheduler apps/dashboard apps/accounts/tests/test_manual.py -q` — 0 failed
- `rg -n "Unidade 1|Unidade 2" templates/` → 0 matches (labels literais eliminados dos templates; defaults vivem em settings/código)
- `uv run ruff check apps config && uv run ruff format --check apps config` — ok
- `uv run mypy apps` — ok

## Critérios de aceitação

- [ ] R1–R5 verdes conforme a matriz
- [ ] Nenhum arquivo fora do blast radius
- [ ] RED demonstrado antes do GREEN (mesmo comando)
- [ ] Testes existentes de manual/dashboard passam SEM edição (defaults preservados)
