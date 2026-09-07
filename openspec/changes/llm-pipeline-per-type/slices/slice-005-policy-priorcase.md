# Slice 005: Policy consultiva determinística + prior-case

## Objetivo

As duas determinações que alimentam a decisão: `policy.py` (avaliação clínica determinística por procedimento — thresholds S1–S8 do catálogo + requisitos gerais — com `ok|alerta|nao_informado` e recomendação **consultiva**) e `prior_case.py` (casos prévios por nº de ocorrência 7d + fallback nome normalizado+nascimento 15d, por procedimento, auditável). Ambos com persistência nos campos/eventos do Case.

## Contexto necessário (contexto zero)

- Fonte clínica: `temp/plano-implementacao-hmd.md` §2 (thresholds por seção — espelhados em `apps/cases/procedure_catalog.py::CRITERIA_SECTIONS`; requisitos gerais com protocolo de anticoagulantes; comportamento da policy: `ok|alerta|nao_informado` + recomendação global com motivos, **nunca bloqueia**) e §8 (prior-case).
- Change 03: `procedure_catalog` (seção do tipo, `anesthetic_support`); `CaseProcedure` (`doctor_disposition/reason/decided_at` — **campos semânticos**, não status FSM — lição "after closure" do ats-web); `patient_name`/`patient_birth_date`/`agency_record_number` no Case (persistidos pelo change 05).
- Referências ats-web (SOMENTE-LEITURA): `apps/pipeline/policy/eda_policy.py` (estrutura de resultado) e `apps/pipeline/prior_case.py` (`PriorCaseSummary`, ordem fechada de decisão por `doctor_disposition`/`appointment_status` — HMD usa só `doctor_disposition` porque agendamento ainda não existe).
- Nome normalizado: Postgres `unaccent` (habilitado no compose desde o change 01).

## Requisitos

- **R1** `evaluate_preop_policy(structured_data, procedure_type) -> PolicyResult` — **pura** (sem LLM/DB): por critério da seção do tipo (Plt/INR/Hb/Cr/PAS/glicemia/K + condições especiais): `ok` (dentro), `alerta(motivo)` (fora), `nao_informado` (campo ausente/`incerto`); **requisitos gerais** avaliados sempre: anticoagulante em uso → alerta com protocolo de suspensão POR FÁRMACO (Marevan/Marcoumar/Cumadin/varfarina/Pradaxa 7d; Xarelto 3d; Eliquis/Lixiana 48h; enoxaparina/heparina SC 12h profilática/24h terapêutica — matching por nome/fabricante case-insensitive no texto das medicações); antiagregante → alerta informativo ("em geral não suspender"); metformina 48h; alergia contraste/frutos/iodo → protocolo dessensibilização; peso > 180 kg; jejum 8h (checklist); isolamento (sinalizar unidade); Cr ≥ 1,5 → nefroproteção + liberação Nefrologia; `anesthetic_support` do tipo → alerta de suporte anestésico.
- **R2** Recomendação global por procedimento — **tabela de critérios explícita em `policy.py` (dados versionados, correção do review)**: cada critério de seção (S1–S8) mapeia `path do schema → operador → threshold do catálogo → severidade`. Severidade determinística: **critério de seção fora do threshold → motivo de recusa** (`recomenda_recusar` se ≥1); **requisitos gerais → sempre alertas informativos com protocolo** (nunca recusam sozinhos); `nao_informado` nunca recusa. Condições textuais de seção com valor numérico extraído → comparadas; sem valor → `nao_informado`. **Nunca bloqueia** — recomendação+explicação.
- **R3** Wrapper `evaluate_case_policies(case, *, user, role)`: roda por procedimento **declarado** sobre `case.structured_data`; persiste `Case.policy_result` (JSON por tipo) + evento `CASE_POLICY_EVALUATED` (resumo por tipo).
- **R4** `lookup_prior_case_context(case, procedure_type) -> PriorCaseSummary | None`: candidatas = outros casos com `CaseProcedure` do mesmo tipo e `doctor_disposition != pending` (com `doctor_decided_at`); chave primária `agency_record_number` igual (não-vazio) e decisão há ≤ `PRIOR_CASE_WINDOW_DAYS` (default 7); **fallback**: `normalize_name(patient_name)` (unaccent+upper/sem espaços) e `patient_birth_date` iguais (não-nulos) e **intervalo FECHADO** `decisão_prévia <= case.created_at <= decisão_prévia + PRIOR_CASE_FALLBACK_WINDOW_DAYS` (15) — não aceita caso "futuro" (correção do review); prioridade nº > fallback; escolhe decisão mais recente; resumo com data/decisão/**motivo anonimizado pelo núcleo `anonymize_text` do change 05** (motivo é texto livre do médico e pode conter PII — o original só vai à UI do médico no 07) + `prior_denial_count`. Sem match → None. **Nota**: até o change 07 não existem decisões no banco — lookup vazio em produção inicial (esperado; testes com fixtures).
- **R5** Wrapper `record_prior_case_lookups(case, *, user, role)`: por procedimento declarado; grava evento `PRIOR_CASE_LOOKUP` (origem `occurrence_number|name_birthdate_fallback|none`, resumo enxuto — sem PII além do que já está no caso).
- **R6** Settings/env: `PRIOR_CASE_WINDOW_DAYS` (7), `PRIOR_CASE_FALLBACK_WINDOW_DAYS` (15) + `.env.example`. (Campos/eventos já criados no slice 004 — dono único.)
- **R7** Testes: policy pura por seção (parameterized S1–S8 com valores limite dentro/fora/ausente); anticoagulantes por fármaco (protocolo correto por nome, inclusive Pradaxa); antiagregante informativo; metformina/alergia/peso/jejum/isolamento/Cr/anestésico (parameterized); recomendação aceitar/recusar com motivos; `nao_informado` distinto; wrapper persiste+eventa; prior-case: nº dentro/fora de 7d; fallback dentro/fora de 15d; nº diferente + nome/nascimento iguais → fallback; próprio caso excluído; sem decisão → ignorado; eventos com origem correta.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1/R2 | `apps/pipeline/policy.py` | `test_policy.py::test_section_thresholds_parameterized`, `::test_anticoagulant_protocols_per_drug`, `::test_general_requirements_parameterized`, `::test_recommendation_accept_and_refuse` |
| R3 | `apps/pipeline/policy.py` (wrapper) | `::test_wrapper_persists_and_events` |
| R4 | `apps/pipeline/prior_case.py` | `test_prior_case.py::test_match_by_number_7d`, `::test_no_match_outside_7d`, `::test_fallback_name_birthdate_15d`, `::test_fallback_outside_15d`, `::test_excludes_self_and_undecided` |
| R5 | `apps/pipeline/prior_case.py` (wrapper) | `::test_lookup_events_with_origin` |
| R6 | `config/settings/base.py`, `.env.example` | `rg -n "PRIOR_CASE_" config/settings/base.py .env.example` |
| R7 | `apps/pipeline/tests/{test_policy,test_prior_case}.py` | `uv run pytest apps/pipeline/tests/test_policy.py apps/pipeline/tests/test_prior_case.py` |

## RED

- Comando: `uv run pytest apps/pipeline/tests/test_policy.py`
- Falha esperada: `ModuleNotFoundError: apps.pipeline.policy`.

## GREEN / verificação local

- Os 2 arquivos de teste — exit 0
- `uv run pytest apps/pipeline/tests/` — exit 0
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .`

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/pipeline/policy.py
  - apps/pipeline/prior_case.py
  - apps/pipeline/tests/{test_policy,test_prior_case}.py
  - config/settings/base.py
  - .env.example
allowed_incidental_files: []
out_of_scope:
  - LLM2 (006 — policy/prior o alimentam); UI; prior-case por agendamento (08 ainda não existe); cache de lookups
```

Escale ao parent se: o mapeamento "alerta↔recusa" de alguma seção for ambíguo no documento clínico.

## Critérios de aceitação

- [ ] R1–R7 comprovados; policy pura determinística (mesma entrada → mesma saída)
- [ ] Protocolo de anticoagulantes fiel ao documento (por fármaco)
- [ ] Prior-case com prioridade nº>fallback e janelas corretas; eventos auditáveis
- [ ] Gate parcial do slice verde
