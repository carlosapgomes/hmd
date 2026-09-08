# Slice 003: Presenter re-identificado + PDF original + prior-case real

## Objetivo

Detalhe do caso para o médico: presenter que re-identifica os artefatos do
pipeline (identificação real, histórico/sumário, estrutura, alertas da policy
+ recomendação por procedimento, agregado), card de prior-case com motivo
real, e acesso ao PDF original — tudo sob o access control do slice 002,
com a barreira "LLM só vê tokens" intacta (re-identificação exclusivamente na
renderização; nenhuma chamada LLM).

## Contexto necessário

- `apps/anonymization/reidentify.py` — `reidentify(text, pseudonym_map)` /
  `reidentify_text(case, text)`; `Case.pseudonym_map` (token →
  `{"value", "entity_type"}`).
- Artefatos persistidos pelo change 06 (formatos exatos, verificados no
  código):
  `Case.structured_data` (artefato LLM1, tokens; chaves do schema base em
  `apps/pipeline/schemas/base.py`: `pedido`, `contexto_clinico`,
  `linha_do_tempo`, `exames`, `medicacoes`, `comorbidades`,
  `contraindicacoes`, `trechos_nao_classificados`),
  `Case.policy_result`
  (`{tipo: {procedure_type, section_id, recommendation, refusal_reasons,
  criteria: [{criterion, status, severity, reason}]}}` — atenção: a chave do
  critério é **`criterion`**),
  `Case.suggested_action` (`{procedures: {tipo: {suggestion, motivos}},
  aggregate: {suggestion, motivos}}` — atenção: **`motivos`**, não
  `reasons`), `Case.summary_text` (tokens).
- `apps/pipeline/prior_case.py::lookup_prior_case_context(case,
  procedure_type) -> PriorCaseSummary | None` (**read-only**; motivo vem
  ANONIMIZADO; `prior_case_id` identifica o caso prévio) — o card busca o
  motivo REAL na `CaseProcedure` do caso prévio (`doctor_reason`).
- `apps/cases/models.py::CaseDocument` (PDF por `position`) e
  `apps/cases/procedures.py::get_declared_procedure_types`.
- `apps/doctor/views.py` + access control do slice 002 (`apps/doctor/access.py::
  can_access_case` — reutilizar, não duplicar).
- Padrão de template: `templates/intake/case_detail.html` (cards/badges).
- Design D3/D7 (`openspec/changes/doctor-queue-decision/design.md`).
- Padrão de resposta de arquivo: `FileResponse` (Django) — NÃO copiar o
  viewer embutido do ats-web.

## Requisitos verificáveis

- **R1** `reidentify_structure(value, pseudonym_map)` recursivo em
  `apps/anonymization/reidentify.py` (dict/list/str → re-identifica strings;
  outros tipos atravessam; imutável — devolve nova estrutura; mapa vazio →
  estrutura inalterada) + testes no app anonymization.
- **R2** `apps/doctor/presenters.py::build_case_detail_context(case)` puro
  (sem request): identificação real (nome/nº ocorrência/nascimento quando
  presentes), tipos declarados com subtipo e disposição atual,
  `summary_text` re-identificado, `structured_data` re-identificado por
  seção (pedido, contexto_clinico, linha_do_tempo, exames, medicações,
  comorbidades, contraindicações, trechos_nao_classificados — não renderizar
  bruto), alertas da policy por procedimento (motivos de recusa + critérios
  em alerta) com recomendação e sugestão/agregado do LLM2 (usando as chaves
  reais `criterion`/`motivos`), **requisitos gerais acionáveis** (protocolos
  de suspensão/dessensibilização/nefroproteção conforme os alertas do caso),
  prior-case por tipo com motivo real, e flag `can_decide` (= estado
  `AWAITING_DOCTOR`).
- **R3** View `doctor:case_detail` (`/doctor/case/<id>/`): guard
  `role_required("doctor", "admin")` + access control por subtipo (403 fora da
  matriz D2, **sem** expor dados); renderiza o contexto do presenter; estados
  `AWAITING_DOCTOR` e pós-decisão são aceitos (decisão em si é o slice 004 —
  aqui o formulário NÃO existe).
- **R4** View `doctor:case_pdf` (`/doctor/case/<id>/pdf/<position>/`):
  `FileResponse` do `CaseDocument`; 404 sem documento/position; mesmo guard
  (doctor/admin + subtipo).
- **R5** Template `templates/doctor/case_detail.html`: cards por seção
  (identificação, alertas consultivos por procedimento com destaque visual
  para recomendação de recusa, prior-case, sumário, estrutura, documentos com
  link por position).
- **R6** Testes: presenter puro (tokens substituídos nos 4 artefatos; caso
  sem artefatos não explode — seções vazias), guard de view (403 por papel e
  por subtipo), PDF (200 application/pdf, 404), prior-case com motivo real
  (não o anonimizado).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/anonymization/reidentify.py`, `apps/anonymization/tests/test_reidentify_structure.py` | `test_recursive_*`, `test_immutable`, `test_empty_map` |
| R2 | `apps/doctor/presenters.py` | `test_presenter_*` (tokens re-identificados; policy/agregado; prior real) |
| R3 | `apps/doctor/views.py` | `test_detail_*` (200/403/404 por papel e subtipo) |
| R4 | `apps/doctor/views.py` | `test_pdf_served`, `test_pdf_404` |
| R5 | `templates/doctor/case_detail.html` | `test_detail_renders_cards` |
| R6 | `apps/doctor/tests/test_detail.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/anonymization/reidentify.py            # aditivo: reidentify_structure
  - apps/anonymization/tests/test_reidentify_structure.py
  - apps/doctor/presenters.py
  - apps/doctor/views.py
  - apps/doctor/urls.py
  - apps/doctor/tests/test_detail.py
  - templates/doctor/case_detail.html

out_of_scope:
  - formulário/POST de decisão (slice 004)
  - qualquer chamada LLM; persistência de artefatos re-identificados
  - anexos com OCR (change 10)
  - mudanças nos workers/signals
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/doctor/tests/test_detail.py apps/anonymization/tests/test_reidentify_structure.py`
- Falha esperada: `ImportError: cannot import name 'build_case_detail_context'`
  e `ImportError: cannot import name 'reidentify_structure'`.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/doctor/tests/ apps/anonymization/tests/`
  — exit 0.
- `uv run ruff check apps/doctor apps/anonymization && uv run ruff format --check apps/doctor apps/anonymization`
- `uv run mypy .`

## Critérios de aceitação

- [ ] R1–R6 comprovados; nenhum token **do mapa do caso** sobrevive na
      renderização dos artefatos (assert em teste com mapa populado; tokens
      de espaços alheios — ex. motivos prior-case re-anonimizados — podem
      sobrar como tokens, sem vazamento)
- [ ] 403 do detalhe não vaza dados do paciente (corpo sem nome/registro)
- [ ] Gate parcial do slice verde
