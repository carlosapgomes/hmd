# Slice 002 — Lista do painel com paridade ats-web (cards + filtros + default)

```yaml
expected_files:
  - apps/dashboard/views.py
  - apps/dashboard/urls.py
  - apps/dashboard/case_labels.py
  - templates/dashboard/home.html
  - templates/dashboard/case_detail.html
  - apps/dashboard/tests/test_views.py
```

## Contexto necessário

- `apps/dashboard/views.py` `home` (~248-291): métricas (`period`
  hoje/7d/30d/tudo — INALTERADO) + lista com `scope` (default `ativos`) /
  `status` / `q` (≥3 chars: nº ocorrência ou prefixo do uid) / paginação
  25 / encerramento admin no card; `DEFAULT_SCOPE = "ativos"` (~81);
  `CASE_NEXT_STEP_LABELS` em `apps/dashboard/case_labels.py` (fase).
- Molde ats-web `_dashboard_case_list_context`/
  `_resolve_list_defaults` (apps/dashboard/views.py do ats-web ~660-760):
  filtros compõem por AND; default sem filtros explícitos =
  hoje/hoje + todos os estados; `created_at__date` gte/lte.
- Campos: `patient_name`/`patient_age`/`origin_unit` (slice 001) +
  `agency_record_number`/`created_at`/`procedures`.
- `PROCEDURE_PROFILES` (13 tipos) em `apps/cases/procedure_catalog.py`.
- Templates: `templates/dashboard/home.html` (cards atuais: nº ocorrência,
  uid curto, status, tipos, criado em, resultado, próximo passo, link
  encerrar).

## Goal

Cards com identificação completa (nome/idade/unidade/exames/fase/data-hora
+ [Detalhes] rota futura), filtros data range + tipo de exame + busca por
nome, default hoje/todos os estados; métricas e encerramento intactos.

## Deliverables

### R1 — Filtros e default (views)

- `date_from`/`date_to` (ISO; `created_at__date__gte/lte`; **inválido =
  ausente; from > to normaliza por swap**), compondo por
  AND com scope/status/q/procedure_type.
- `procedure_type` (dropdown 13 tipos; `declared/all` default; filtro
  `procedures__declared_by_nir=True, procedures__procedure_type=X` +
  `distinct()`).
- `q` passa a incluir `patient_name__icontains` (OR com ocorrência/prefixo
  uid; ≥3 chars).
- Default (molde `_resolve_list_defaults`): SEM nenhum de
  `scope/status/procedure_type/q/date_from/date_to` explícito →
  `date_from=date_to=hoje` e `scope=todos`; qualquer explícito preservado.
  `DEFAULT_SCOPE` → `todos` (para o caso de datas explícitas sem scope).
- `period` das métricas permanece independente (inalterado).

### R2 — Cards (template) + rota de detalhe (view mínima real)

- Cards: nome (`—` quando ausente) + idade (`is not None`; `0 a` exibe) +
  unidade de origem (quando presente) + nº ocorrência + exames declarados
  + fase (status badge + próximo passo, como hoje) + data/hora de inserção
  absoluta (`d/m/Y H:i`) + botão **[Detalhes]** (`{% url 'dashboard:case_detail' item.case_id %}`).
- Encerramento administrativo: link sai do card e passa a viver no DETALHE.
- Este slice cria a rota `dashboard:case_detail`
  (`case/<uuid:case_id>/`) e a view REAL com: identificação completa (nome,
  idade, sexo, raça/cor, unidade de origem, nº ocorrência, data/hora de
  inserção, fase/status), procedimentos declarados e a ação de encerramento
  administrativo (casos ≠CLEANED, rotas `admin_close_confirm` existentes).
  Guard manager/admin; caso inexistente → 404. A TRILHA (labels/collapsible)
  é adicionada pelo slice 003 neste MESMO template.

### R3 — Testes (RED→GREEN)

- Novos/ajustados (RED): default sem filtros → casos de HOJE em TODOS os
  estados (incl. CLEANED); `date_from`/`date_to` filtram e compõem por
  AND; `procedure_type` filtra por tipo declarado (dropdown presente com
  os 13); `q` por nome do paciente (encontra; <3 chars ignora);
  datas ISO inválidas ignoradas (ausentes) e `from > to` swap normalizado;
  **links de `period` preservam os filtros da lista** (query string
  estendida) e a paginação preserva os filtros NOVOS;
  card com nome+idade+unidade+exames+fase+data/hora+[Detalhes] (href
  pinnado); `0 a` exibe; ausentes `—`/omitidos; encerramento FORA do card;
  **detail novo**: manager/admin vê identificação completa + procedimentos
  + ação de encerramento (≠CLEANED; `CLEANED` sem); papel fora de
  manager/admin → 403; caso inexistente → 404; métricas inalteradas por
  filtros da lista (pin: `period` segue controlando métricas); paginação
  preserva os filtros NOVOS.
- Atualizar testes existentes do default antigo (hoje+ativos).
- Bateria completa do AGENTS.md.

## Gates para o reviewer (2 linhas)

1. O default novo é pinnado discriminantemente: casos hoje-CLEANED +
  ontem-ativo → sem filtros mostra SÓ o de hoje (incluindo CLEANED); com
  `?date_from=<ontem>` mostra os dois (prova que o default aplica datas e
  scope=todos, não herda o period das métricas).
2. Busca por nome não vaza entre escopos: `q` com nome de caso fora do
  período/filtro não aparece (composição AND).

## Out of scope

- Trilha/labels (slice 003); remoção da trilha do NIR/médico (slice 003);
  métricas; X-ATS-Partial; badge atenção.
