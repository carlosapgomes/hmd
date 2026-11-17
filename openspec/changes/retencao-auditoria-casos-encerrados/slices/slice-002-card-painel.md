# Slice 002 — Card «Sumário apresentado ao médico» no painel

**Pré-requisito**: slice 001 (usa caso encerrado em modo retenção como
fixture do teste de caso `CLEANED`).

```yaml
expected_files:
  - apps/cases/presentation.py
  - apps/doctor/presenters.py
  - apps/dashboard/presenters.py
  - apps/dashboard/views.py
  - templates/dashboard/case_detail.html
  - apps/dashboard/tests/test_summary_card.py
```

*(Run-only, sem edição esperada: `apps/doctor/tests/test_detail.py` — a
suíte médica deve passar INTACTA como prova de que a extração não mudou
comportamento.)*

## Contexto necessário

- `apps/doctor/presenters.py`: builders de seção privados
  (`_STRUCTURE_SECTION_KEYS`/`_STRUCTURE_SECTION_TITLES`,
  `_SECTION_BUILDERS` + `_pedido_lines`…`_trechos_nao_classificados_lines`,
  helpers `_as_str`/`_as_list`) e mapas de que dependem
  (`_PROFILE_BY_TYPE` L60, `_EXAM_LABELS` L124-133); quem monta as 8 seções
  é `_build_structure_sections` (L286-298). `summary_lines` = linhas
  não-vazias de `reidentify_text(case, case.summary_text)`.
- `apps/anonymization/reidentify.py`: `reidentify_text` /
  `reidentify_structure` (D8: consumers autorizados a re-identificar —
  doctor/manager/admin — controlam acesso).
- `templates/doctor/_case_cards.html` (~L103-146): markup dos cards
  «Sumário clínico» (`<p>` por linha) e «Estrutura extraída» (`<h3>` por
  seção + `<ul>` de linhas), placeholders **independentes por bloco**.
- `apps/dashboard/views.py::_case_detail_context`: contexto do detalhe
  (identificação, procedimentos, documentos, anexos, trilha, encerramento).
- `templates/dashboard/case_detail.html`: cards em ordem — Identificação →
  Procedimentos declarados → Texto Extraído → PDF Original → Anexos →
  Trilha → Encerramento; collapses Bootstrap fechados por padrão.
- Molde de teste: `apps/dashboard/tests/test_detail_cards.py` (HTTP,
  login manager com `active_role`, asserções de HTML).

## Goal

Supervisor (manager/admin) vê, no detalhe do caso do painel, o MESMO
sumário clínico e a MESMA estrutura extraída apresentados ao médico —
card único collapsible fechado por padrão, re-identificado, cobrindo
casos ativos e encerrados retidos; placeholders explícitos por bloco sem
artefatos.

## Deliverables

### R1 — Builders compartilhados com API pública única (D5)

- Novo `apps/cases/presentation.py`: movers puros — os 8 builders de
  linha, chaves/títulos das seções, helpers `_as_str`/`_as_list` E os
  mapas dependentes (`_PROFILE_BY_TYPE`/`_EXAM_LABELS`, derivados de
  `PROCEDURE_PROFILES` localmente, sem import do app médico) — expondo
  **uma API pública única** `build_structure_sections(structured) ->
  list[dict]` que os dois presenters consomem (o loop de montagem NÃO é
  reimplementado no painel). Sem dependência de `apps.anonymization` nem
  I/O. `apps/doctor/presenters.py` passa a importar de lá — EXTRAÇÃO PURA,
  `build_case_detail_context` inalterado.

### R2 — Presenter do painel

- `apps/dashboard/presenters.py::build_case_summary_context(case)`:
  `summary_lines` + `sections` (API compartilhada sobre
  `reidentify_structure`) + `has_structure`, re-identificação só na
  renderização com o mapa do caso; consumido por `_case_detail_context`.

### R3 — Card no template (D6)

- Card «Sumário apresentado ao médico» (título FIXADO no design — não
  reabrir) entre «Procedimentos declarados» e «Texto Extraído do PDF»;
  collapse fechado por padrão; dentro, blocos «Sumário clínico» e
  «Estrutura extraída» com o MESMO markup dos cards médicos; placeholders
  explícitos **independentes por bloco** (caso com estrutura sem sumário
  mostra a estrutura + placeholder só no sumário).

### R4 — Testes (RED→GREEN)

- Novos (RED primeiro): manager logado vê card fechado + conteúdo
  re-identificado (valor real presente, `<PESSOA_` ausente no HTML); caso
  `CLEANED` retido (encerrado com flag desativado — fixture do slice 001)
  renderiza o conteúdo; caso sem artefatos exibe placeholders POR BLOCO
  (incluindo estrutura-sem-sumário); papel ativo nir/doctor/scheduler
  → 403 na rota; paridade — `summary_lines`/`sections` do painel IGUAIS
  aos do `build_case_detail_context` médico no mesmo caso.
- Bateria: `TEST_DB_PORT=55435 uv run pytest -q apps/dashboard apps/doctor`
  + suíte completa + gate completo do AGENTS.md.

## Gates para o reviewer (2 linhas)

1. `git diff` de `apps/doctor/presenters.py` é PURA EXTRAÇÃO (mover mapas +
   builders + import; `build_case_detail_context` inalterado) e a suíte
   médica passa sem NENHUMA edição de teste existente (run-only).
2. Paridade provada por teste que compara o contexto do painel com o do
   médico no mesmo caso (mesmas linhas/mesmas seções) + o painel consome a
   MESMA API pública `build_structure_sections` (sem loop reimplementado).

## Out of scope

- Mudanças no detalhe médico/agendador/NIR (o card é aditivo no painel).
- Alertas consultivos/policy/prior-case/requisitos no painel (outro
  escopo, se um dia o dono pedir).
- Purga retroativa (slice 003).
