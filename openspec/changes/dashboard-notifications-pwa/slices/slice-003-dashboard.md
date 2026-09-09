# Slice 003: Dashboard gerencial — métricas por período/tipo/unidade

## Objetivo

App `apps/dashboard` com services de métricas puros (fontes imutáveis),
view `dashboard:home` com seletor de período e template zero-PHI, link
"Painel" na navbar.

## Contexto necessário

- Design D3 (`openspec/changes/dashboard-notifications-pwa/design.md`).
- Fontes imutáveis (lição ats-web `_compute_summary`; **emenda P1 review**
  — os nomes de campos abaixo são os REAIS):
  - População: casos com `created_at` no período, SEMPRE.
  - Outcome por caso: `payload["source"]` do **ÚLTIMO** evento
    `CASE_STATUS_FINAL_REPLY_POSTED` do caso (grep `apps/cases/events.py` +
    payload em `apps/cases/services.py::post_final_reply`) **apenas quando
    o status atual é pós-final** (`FINAL_REPLY_POSTED`, `AWAITING_NIR_ACK`,
    `CLEANING`, `CLEANED` — caso reaberto/em voo é "em andamento";
    elimina dupla contagem de 2 eventos finais com sources distintos e
    `em_andamento` negativo). Agendado = `SCHEDULING_CONFIRMED`; negado =
    `DOCTOR_DENIED` ∪ `SCHEDULING_DENIED`.
  - Tipo/decisão: `CaseProcedure` — o campo é **`doctor_disposition`**
    (`approved|denied|pending`) e `doctor_decided_at` é **por row**
    (`Case` NÃO tem `doctor_decided_at` — não invente o campo).
  - Unidade: `Case.scheduled_unit` atual (`SchedulingUnit` 1/2,
    preservado pós-limpeza do 09) dos casos com outcome agendado.
  - Tempo até decisão: por caso, `max(CaseProcedure.doctor_decided_at) −
    Case.created_at`.
- Períodos: `hoje` (dia local — helper de bounds do repo? veja
  `apps/intake`/`timezone.localtime`; senão implemente `local_day_bounds`
  no metrics), `7d`, `30d`, `tudo` (default `hoje`).
- Catálogo canônico: `apps/cases/procedure_catalog.py` (ordem + labels —
  NÃO recopiar; importe).
- App novo: padrão `apps/attachments/apps.py` (registro em
  `INSTALLED_APPS`); URL namespace `dashboard:`; view login-required (sem
  `role_required` — painel é transversal, D3).
- Referência visual: ats-web `apps/dashboard` SOMENTE-LEITURA (cards
  Bootstrap de resumo + tabela) — adapte, não copie a domínio ATS.
- Timezone: repo usa `TIME_ZONE` local — use `timezone.localtime()` para
  bounds de dia (padrão dos testes de workers).

## Requisitos verificáveis

- **R1** `apps/dashboard/metrics.py` (services puros, sem request):
  `_period_bounds(period)` (hoje= dia local [00:00, +1d); 7d/30d= janela
  rolling até agora; tudo= None); `compute_summary(period)` → dict com
  `total`, `agendados` (eventos source `SCHEDULING_CONFIRMED` no período,
  casos distintos), `negados` (source `DOCTOR_DENIED` ∪
  `SCHEDULING_DENIED`, casos distintos), `em_andamento` (total−agendados−
  negados), `encerrados` (status `CLEANED` no total do período);
  `compute_by_procedure_type(period)` → linhas por tipo do catálogo
  (ordem canônica) com total/aprovados/negados/sem decisão;
  `compute_by_unit(period)` → {unidade 1, unidade 2} dos casos com outcome
  agendado (por `scheduled_unit`); `compute_avg_time_to_decision(period)` →
  `timedelta | None` (média de `max(CaseProcedure.doctor_decided_at por
  caso) − case.created_at` dos casos da população com row decidida no
  período — `doctor_disposition ≠ pending`).
  **Nota (P2 review)**: a tabela por tipo conta ROWS de procedimento —
  somatório da tabela ≠ total do resumo (esperado); NÃO assertar igualdade
  de somas no teste, e a population do resumo é por `created_at` (o
  outcome pode vir de evento posterior à janela).
- **R2** View `dashboard:home`: login required; GET `?period=`
  validado contra `{hoje,7d,30d,tudo}` (inválido → default hoje);
  contexto com summary/tipos/unidade/tempo humanizado
  (`"3h 42m"`/`"—"` quando sem decididos) + período ativo.
- **R3** Template `templates/dashboard/home.html`: cards de resumo,
  tabela por tipo (labels do catálogo), linha por unidade, tempo médio;
  seletor de período (links GET preservando path); **zero-PHI** — nenhum
  campo de paciente renderizado.
- **R4** Navbar: link "Painel" (`{% url 'dashboard:home' %}`) sempre
  visível para autenticado (sem gate de papel).
- **R5** Testes (com fixtures dirigidos): resumo coerente (2 agendados
  [1 unidade 1, 1 unidade 2], 1 negado médico, 1 negado agendamento, 2 em
  andamento); **caso reaberto por intercorrência conta como EM ANDAMENTO
  apesar do evento final anterior** (emenda P1 review); caso CLEANED
  continua contado pelo outcome; períodos (caso de ontem fora de `hoje`,
  dentro de `7d`); tabela por tipo com decisões parciais (por
  `doctor_disposition`); tempo médio calculado; página renderizada SEM
  nome/nº registro dos pacientes do fixture (assert zero-PHI); anônimo →
  redirect login; período inválido → hoje.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/dashboard/metrics.py` | `test_summary_counts`, `test_summary_uses_event_source`, `test_by_type_*`, `test_by_unit_*`, `test_avg_time_*`, `test_period_bounds_*` |
| R2 | `apps/dashboard/views.py`, `apps/dashboard/urls.py`, `config/urls.py` | `test_invalid_period_defaults`, `test_anonymous_redirect` |
| R3 | `templates/dashboard/home.html` | `test_dashboard_renders_zero_phi` |
| R4 | `templates/base.html` | `test_navbar_link` |
| R5 | `apps/dashboard/tests/test_metrics.py`, `apps/dashboard/tests/test_views.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/dashboard/{__init__,apps,urls,views,metrics}.py
  - apps/dashboard/tests/{__init__,test_metrics,test_views}.py
  - templates/dashboard/home.html
  - templates/base.html          # link Painel
  - config/settings/base.py      # INSTALLED_APPS
  - config/urls.py               # include dashboard

out_of_scope:
  - notificações (slices 001/002); PWA; manual; apps/cases/apps/intake (nenhuma mudança)
  - métricas ATS (admissões/attention/etc.); export CSV/PDF; cache
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/dashboard/tests/test_metrics.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.dashboard'`.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/dashboard/tests/ apps/accounts/tests/` — exit 0
- `uv run ruff check apps/dashboard config && uv run ruff format --check apps/dashboard config`
- `uv run mypy .`
- `--create-db` UMA vez se a migration do app novo exigir (sem models próprios → não deve precisar).

## Critérios de aceitação

- [ ] R1–R5 comprovados; fontes IMUTÁVEIS (caso CLEANED conta; nada
      depende só do status FSM corrente)
- [ ] Zero-PHI comprovado por teste de renderização
- [ ] Ordem canônica importada do catálogo (sem cópia)
- [ ] Gate parcial do slice verde
