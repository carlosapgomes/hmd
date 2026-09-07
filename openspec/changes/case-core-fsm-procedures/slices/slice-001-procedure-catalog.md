# Slice 001: Catálogo de procedimentos (code-first) + verificação

## Objetivo

O catálogo dos 13 tipos como código consultável e verificável: `apps/cases/procedure_catalog.py` com `ProcedureProfile` (label, seção de critérios, subtipo doctor, suporte anestésico), tabela `CRITERIA_SECTIONS` (S1–S8) e lookup fail-fast; comando `seed_procedure_catalog` que valida a coerência do registro (idempotente por natureza).

## Contexto necessário (contexto zero)

- Fonte clínica autoritativa: `temp/plano-implementacao-hmd.md` §2 (tabela dos 13 tipos com seção/subtipo + thresholds por seção S1–S8 + requisitos gerais com lista de suporte anestésico) e `temp/parametrosHMD.md` (documento original do hospital).
- Design: `../design.md` D3 (code-first, fail-fast, verificação idempotente; desvio do "valores no banco" registrado).
- Spec: `../specs/case-management/spec.md` — Requirement "Catálogo de 13 tipos de procedimento" (3 cenários).
- Padrão de referência (somente-leitura): `/projects/dev/ats-web/apps/cases/exam_profiles.py` (`@dataclass ExamProfile` + registro `_PROFILES_BY_EXAM_TYPE` + getter com fallback — **HMD diverge**: fail-fast, sem fallback).
- App `apps/cases` não existe ainda — este slice a cria (apps.py, `__init__.py`, INSTALLED_APPS em `config/settings/base.py`). Modelos chegam no slice 002; este slice é 100% código puro + comando.
- Mapeamentos fixos (da fonte clínica): flebografia usa **S1**; angio_carotidas usa **S2**; subtipos: angio×8 (`art_perif, flebografia, angio_art_perif, angio_venosa_central, angio_fav, permicath, filtro_cava, angio_carotidas`), neuro (`art_cerebral`), cardio×2 (`cat_cardiaco, angio_coronariana`), radio×2 (`dren_biliar, nefrostomia`).

## Requisitos

- **R1** `ProcedureProfile` (dataclass frozen): `procedure_type: str`, `label: str`, `criteria_section: str` (`"S1".."S8"`), `doctor_subtipo: str` (`angio|neuro|cardio|radio`), `anesthetic_support: bool`.
- **R2** Registro ordenado dos 13 tipos exatamente como na tabela da fonte clínica (nomes canônicos `art_perif` … `nefrostomia`), acessível por iteração e por lookup.
- **R3** `get_procedure_profile(procedure_type)`: retorna o perfil; tipo desconhecido → exceção explícita (`KeyError`/`ValueError` com nome do tipo) — **sem fallback**.
- **R4** `CRITERIA_SECTIONS`: mapeia `S1..S8` → estrutura com os thresholds da fonte (plat/INR/Hb/Cr/PAS/glicemia/K/condições especiais por seção) e a lista de tipos que usam cada seção; consultável por seção e por tipo.
- **R5** `anesthetic_support=True` para os 11 tipos **interventionais**: as cinco angioplastias (`angio_art_perif`, `angio_venosa_central`, `angio_carotidas`, `angio_coronariana`, `angio_fav`) — leitura de "Angioplastias (Arterial e Venosa)" do documento clínico como as **famílias** arteriais e venosas (o próprio documento agrupa "Angioplastia (carótidas/membros/coronariana)") — mais `permicath`, `filtro_cava`, `dren_biliar`, `nefrostomia`, `cat_cardiaco` e `art_cerebral` ("procedimentos da neurointervenção"). `False` apenas para os diagnósticos `art_perif` (arteriografia) e `flebografia`.
  - *✅ Leitura ampla validada pelo dono (2026-09-07): "Angioplastias (Arterial e Venosa)" = as famílias arteriais e venosas completas — as 5 angioplastias + neurointervenção = True; apenas os diagnósticos (`art_perif`, `flebografia`) = False. Flag do review resolvido; consumo pela policy no change 06.*
- **R6** Comando `seed_procedure_catalog`: valida coerência do registro (13 tipos, sem duplicatas, seções S1–S8 existentes, subtipos válidos, seção↔tipos coerente com `CRITERIA_SECTIONS`) e imprime resumo; exit 0; executável 2× sem efeito colateral.
- **R7** Testes: presença dos 13 (guarda a nota de arquivamento do change 01/P2); campos de cada perfil corretos (parameterized pela tabela da fonte); lookup fail-fast; seções coerentes; comando idempotente (2 execuções).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1–R3 | `apps/cases/procedure_catalog.py` | `test_procedure_catalog.py::test_all_13_present`, `::test_profile_fields_parameterized`, `::test_unknown_type_fails_fast` |
| R4 | `apps/cases/procedure_catalog.py` | `::test_criteria_sections_cover_all_types`, `::test_section_thresholds_match_source` |
| R5 | `apps/cases/procedure_catalog.py` | `::test_anesthetic_support_flags` |
| R6 | `apps/cases/management/commands/seed_procedure_catalog.py` | `::test_verify_command_idempotent` |
| R7 | `apps/cases/tests/test_procedure_catalog.py` | `uv run pytest apps/cases/tests/test_procedure_catalog.py` |

## RED

- Comando: `uv run pytest apps/cases/tests/test_procedure_catalog.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.cases'` — catálogo não existe.

## GREEN / verificação local

- `uv run pytest apps/cases/tests/test_procedure_catalog.py` — exit 0
- `uv run python manage.py seed_procedure_catalog --settings=config.settings.dev` — resumo + exit 0; rodar 2×
- `uv run ruff check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/__init__.py
  - apps/cases/apps.py
  - apps/cases/procedure_catalog.py
  - apps/cases/management/{__init__,commands/{__init__,seed_procedure_catalog}}.py
  - apps/cases/tests/{__init__,test_procedure_catalog}.py
  - config/settings/base.py        # INSTALLED_APPS += apps.cases
out_of_scope:
  - models/migrations (slice 002) — este slice NÃO cria tabelas
  - services/procedures (slice 003)
  - policy engine/thresholds aplicados (change 06 — aqui thresholds são só dados do catálogo)
```

Escale ao parent se: a fonte clínica parecer ambígua em algum mapeamento (seção/subtipo/anestésico); precisar de dependência nova.

## Critérios de aceitação

- [ ] R1–R7 comprovados pelos comandos da matriz
- [ ] Nenhum fallback silencioso no lookup
- [ ] Comando executável 2× sem efeito colateral
- [ ] Gate parcial do slice verde
