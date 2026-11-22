# Slice 001: Catálogo 14 — `neurointerv`, seeds, fixações e pinagens de superfície

## Identity

- **Slice:** 001 — Catálogo + seeds + fixações + pinagens (RED→GREEN com gate completo)
- **Change:** `add-neurointervencao-procedure`
- **Tasks:** `../tasks.md` → S1 (contrato deste slice)
- **Origem:** plano revisado após review independente (NEEDS-REWORK 2026-11-22 — BLOCKERs 1–3 e MINORs 1/3 incorporados: fusão das pinagens neste slice, gate completo obrigatório, inventário estendido)

## Objective

Adicionar o tipo `neurointerv` ao catálogo code-first (perfil S2/neuro/anestésico True, contagem 14), a frase de contexto nos seeds de prompt, atualizar TODAS as fixações mecânicas de contagem/lista, alinhar o texto visível do card de revisão à spec ("14 tipos do catálogo") e pinar por teste as derivações (selects do intake, roteamento da fila médica, detecção do LLM1) — fechando com **quality gate completo verde** (suíte inteira inclusa).

## Read first

- `AGENTS.md` (especialmente §2 — quality gate)
- `openspec/changes/add-neurointervencao-procedure/proposal.md`
- `openspec/changes/add-neurointervencao-procedure/design.md` — D1–D6 (D1 código/label, D2 posição, D3 S2 por referência, D4 frase literal, D6 seeds)
- `openspec/changes/add-neurointervencao-procedure/specs/case-management/spec.md`
- `openspec/changes/add-neurointervencao-procedure/specs/intake-nir/spec.md`
- `apps/cases/procedure_catalog.py`
- `apps/cases/tests/test_procedure_catalog.py`
- `apps/llm/prompts_seed.py` (iteração de `PROMPT_SEED_CONTENTS` sobre `PROCEDURE_PROFILES`)
- `apps/llm/management/commands/seed_prompts.py`
- `apps/llm/tests/test_prompts.py`
- `apps/accounts/tests/test_seed_procedure_review.py`
- `apps/pipeline/tests/test_schemas.py`
- `apps/intake/tests/test_procedure_review_ui.py`
- `apps/intake/tests/test_procedure_combobox.py`
- `apps/doctor/tests/test_queue.py`
- `templates/intake/case_detail.html` (bloco "Revisão do procedimento")

## Requirements

### Catálogo

- **R1** Perfil `ProcedureProfile("neurointerv", "Neurointervenção (embolização de aneurisma ou malformação vascular intracraniana)", "S2", "neuro", True)` em `PROCEDURE_PROFILES`, imediatamente após `art_cerebral` (design D2). Evidência: linha nova em `PROFILE_ROWS` (`test_profile_fields_parameterized`) + ordem em `test_all_14_present`.
- **R2** `EXPECTED_PROCEDURE_COUNT = 14`; registro coerente; saída do comando traz "14" (`test_verify_command_idempotent` inclui `assert "14" in first.getvalue()`). Evidência: `test_registry_is_coherent` + `seed_procedure_catalog` reportando 14 perfis/8 seções.
- **R3** `CRITERIA_SECTIONS["S2"].procedure_types` inclui `"neurointerv"` (referência — nenhum threshold novo, design D3). Evidência: `test_criteria_sections_cover_all_types` (contagens 14) + `EXPECTED_SECTIONS["S2"]` do teste com o tipo.
- **R4** Flags anestésicas: `EXPECTED_ANESTHETIC_SUPPORT` ganha `neurointerv` (12 True; False segue só `art_perif`/`flebografia`) e comentários "11 intervencionais" → 12. Evidência: `test_anesthetic_support_flags`.

### Seeds de prompt

- **R5** `_TYPE_CONTEXT["neurointerv"] = "Tratamento endovascular de aneurisma ou malformação vascular intracraniana por embolização."` (literal exato, design D4). Evidência: `test_prompts.py` — seeds geram `proc.neurointerv.llm1.user`/`proc.neurointerv.llm2.user` com a frase no conteúdo.
- **R6** Contagens de seed 31 em **todos** os pontos: `test_seed_count_29` (3 asserts: total, ativos, `len(PROMPT_SEED_CONTENTS)`), os 3 asserts `== 29` restantes (`test_seed_idempotent`, `test_seed_preserves_edits`, `test_seed_prompts_idempotent_with_verification`), `state["prompts"] == 31` em `apps/accounts/tests/test_seed_procedure_review.py:192`, e help/docstring do comando `seed_prompts` ("31 templates: 2 system + 28 user + 1 attachment").

### Fixações mecânicas fora do catálogo/llm

- **R7** `apps/pipeline/tests/test_schemas.py`: `assert len(enum) == 13` → 14 (`test_schema_enum_covers_full_catalog`); docstrings do arquivo que citam "catálogo (13)"/"10 tipos" atualizadas.
- **R8** `apps/intake/tests/test_procedure_review_ui.py`: `CATALOG_SIZE = 13` → 14; teste `test_select_renders_thirteen_options_and_posts_without_js` renomeado (ex.: `test_select_renders_all_catalog_options_and_posts_without_js`) com docstring sem contagem hardcoded.

### Texto visível (spec intake-nir)

- **R9** `templates/intake/case_detail.html`: texto do card de revisão passa a dizer "um dos **14 tipos do catálogo**" (e comentário HTML adjunto, se citar 13). Evidência: asserção no teste de UI do card (texto renderizado contém "14 tipos do catálogo").

### Pinagens das derivações

- **R10** Intake: select do upload e select da revisão oferecem `neurointerv` com o label completo na posição canônica (após `art_cerebral`). Evidência: fixação explícita em `test_procedure_combobox.py` e `test_procedure_review_ui.py` (opção presente com value/label/posição).
- **R11** Fila médica: médico restrito a `neuro` vê caso com `neurointerv` declarado; médico restrito a `cardio` não vê; caso multiprocedimento (`neurointerv` + tipo de outro subtipo) é visível ao `neuro` pela interseção (matriz D2). Evidência: 2 testes em `apps/doctor/tests/test_queue.py` seguindo o padrão local de fixtures (`_make_awaiting_case` ou `set_declared_procedures` + avanço a `AWAITING_DOCTOR` — usar o helper que a suíte já emprega).
- **R12** Detecção LLM1: `procedimentos_solicitados` aceita `neurointerv` e o schema composto do tipo contém apenas a base comum (nenhum bloco específico). Evidência: teste em `test_schemas.py` (padrão de `test_accepts_any_catalog_type` + asserção de ausência de bloco).

### Higiene dos arquivos tocados

- **R13** Docstrings/comentários dos arquivos tocados por este slice sem literais obsoletos ("13 tipos", "× 13 = 28", "29 prompts", "os 13", "11 intervencionais"). Evidência: grep restrito aos arquivos do blast radius.

## Expected blast radius

```yaml
expected_files:
  - apps/cases/procedure_catalog.py                        # perfil + contagem + S2 + docstring do módulo
  - apps/cases/tests/test_procedure_catalog.py             # EXPECTED_ORDER/ANESTHETIC/SECTIONS, PROFILE_ROWS, contagens, header
  - apps/llm/prompts_seed.py                               # _TYPE_CONTEXT + docstring (29/×13)
  - apps/llm/management/commands/seed_prompts.py           # help + docstring (29→31, 26→28)
  - apps/llm/tests/test_prompts.py                         # contagens 29→31 (6 asserts), nomes/conteúdo do tipo, comentários
  - apps/accounts/tests/test_seed_procedure_review.py      # prompts 29→31
  - apps/pipeline/tests/test_schemas.py                    # enum 13→14, docstrings, teste de detecção (R12)
  - apps/intake/tests/test_procedure_review_ui.py          # CATALOG_SIZE, rename de teste, texto do card (R9), opção do select (R10)
  - apps/intake/tests/test_procedure_combobox.py           # pinagem do novo tipo (R10)
  - apps/doctor/tests/test_queue.py                        # 2 testes de roteamento (R11)
  - templates/intake/case_detail.html                      # APENAS texto visível/comentário do card de revisão (R9)
out_of_scope:
  - QUALQUER outra mudança de produção (o template é texto-only — sem lógica/tags novas)
  - migrações; apps/pipeline/schemas/blocks.py; apps/doctor/access.py; apps/intake/forms.py
  - runbook/README/static/config (slice 002); PROJECT_CONTEXT/CHANGELOG (arquivamento)
  - specs/proposal/design/tasks da change
```

Outras fixações de contagem/lista que quebrem no gate: atualizar no mesmo slice e justificar no relatório (expansão esperada e limitada a expectativas fixadas).

## Failing-before plan

Atualizar APENAS testes (nenhum arquivo de produção) e rodar a suíte focada — falhas esperadas (RED):

- `AssertionError` de contagem: 14≠13 (`test_all_14_present`, `test_criteria_sections_cover_all_types`, enum em `test_schema_enum_covers_full_catalog`) e 31≠29 (`test_seed_count_29` + 3 testes irmãos + accounts L192); `"14" in output` no comando.
- `KeyError: procedimento fora do catálogo: 'neurointerv'` nos lookups feitos **pelos próprios testes** (`user_prompt_name("neurointerv", …)`, `get_procedure_profile`).
- `ValueError: procedimento fora do catálogo: 'neurointerv'` nas fixtures de roteamento (`set_declared_procedures`/`CaseProcedure.save`).
- Erro de validação Pydantic no teste de detecção (Literal ainda sem o tipo) e falha de asserção de opção/label nos selects.

Atenção no GREEN: adicionar o perfil a `PROCEDURE_PROFILES` **sem** a chave em `_TYPE_CONTEXT` derruba o import de `prompts_seed` (`KeyError`) e a suíte inteira — perfil e contexto entram na mesma edição.

## Implementation constraints

- S2 por referência (`procedure_types`), nunca seção nova/threshold copiado (design D3).
- Não persistir índices/ordens (`selection_key` e mapas canônicos seguem derivados puros).
- Fail-fast do lookup permanece: nenhum fallback.
- Template: mudança de texto apenas; qualquer necessidade de lógica = escalada.
- Frase de contexto literal conforme R5 (design D4).
- Testes novos seguem fixtures/padrões locais de cada suíte; não enfraquecer asserções existentes.

## Validation

**Focused (worker):**
- `uv run pytest apps/cases/tests/test_procedure_catalog.py apps/llm/tests/test_prompts.py apps/accounts/tests/test_seed_procedure_review.py apps/pipeline/tests/test_schemas.py apps/intake/tests/test_procedure_review_ui.py apps/intake/tests/test_procedure_combobox.py apps/doctor/tests/test_queue.py`
- `uv run python manage.py seed_procedure_catalog --settings=config.settings.dev` (14 perfis/8 seções; 2× — comando somente-leitura)
- `seed_prompts` local é OPCIONAL (escreve no banco dev, exige PostgreSQL ativo); evidência primária de idempotência = testes. NÃO invocar `seed_verify` manualmente (fail-closed fora de settings verify).

**Project-required gates (obrigatórios para encerrar o slice):**
- `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest` — **suíte completa** (o gate NÃO é delegado ao slice 002).

**Runtime/verification evidence:** saída do `seed_procedure_catalog` (resumo 14/8) anexada ao relatório.

## Acceptance criteria

- [ ] R1–R4 catálogo: perfil/posição/label, contagem 14, S2 por referência, flags 12/2
- [ ] R5–R6 seeds: frase literal, 31 em todos os pontos (llm ×6 + accounts + help)
- [ ] R7–R8 fixações: enum 14, CATALOG_SIZE 14, teste renomeado
- [ ] R9 template: "14 tipos do catálogo" com asserção de renderização
- [ ] R10–R12 pinagens: selects, roteamento (neuro vê/cardio não/multiprocedimento), detecção sem bloco
- [ ] R13 arquivos tocados sem literais obsoletos
- [ ] RED documentado antes do GREEN (falhas reais, conforme seção)
- [ ] Gate completo verde (inclui `uv run pytest` inteiro)

## Escalation conditions

Parar como `BLOCKED_NEEDS_DECISION` se: qualquer fixação adicional exigir mudança de produção (além do texto do template); a posição pós-`art_cerebral` quebrar contrato de ordenação persistido; algum teste exigir bloco de schema específico ou threshold diferente de S2; o gate revelar dependência não mapeada do novo tipo.

## Evidence report

`/tmp/hmd-slice-nip-001-report.md`: status (`READY_FOR_REVIEW`/`BLOCKED_NEEDS_DECISION`), R1–R13 → evidência, arquivos alterados, RED (saída real salva) e GREEN, comandos com exit status (inclui gate completo), saída do seed 14/8, expansões de blast radius justificadas, riscos. Não versionar o relatório.

## Worker handoff

```text
Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy in AGENTS.md; smallest correct change,
no speculative abstraction, no unrelated refactoring.
Produce the RED evidence first (tests only), implement, run focused validation,
then the FULL quality gate, and write the evidence report.
Do not update tasks.md, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with BLOCKED_NEEDS_DECISION and evidence.
Otherwise finish with READY_FOR_REVIEW and REPORT_PATH=/tmp/hmd-slice-nip-001-report.md.
```
