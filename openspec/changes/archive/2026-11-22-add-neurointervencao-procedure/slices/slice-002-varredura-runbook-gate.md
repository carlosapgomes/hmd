# Slice 002: Varredura residual ampliada, runbook e gate final

## Identity

- **Slice:** 002 — Higiene de documentação em código/docs + runbook + gate de integração (após 001 aceito)
- **Change:** `add-neurointervencao-procedure`
- **Tasks:** `../tasks.md` → S2 (contrato deste slice)
- **Origem:** plano revisado após review independente (MAJOR-1 grep insuficiente; MAJOR-2 runbook induz a pular seeds)

## Objective

Remover as referências obsoletas de contagem ("13 tipos", "(13)", "× 13", "10 tipos", "11 intervencionais", "26 user", "28/29 templates") de comentários, docstrings, help texts, templates, static, config e README não tocados pelo slice 001; corrigir o comentário do runbook que induz a pular o passo de seeds; e provar a change com o quality gate completo e a verificação operacional dos comandos.

## Read first

- `AGENTS.md` (§2 — quality gate)
- `openspec/changes/add-neurointervencao-procedure/design.md` — D5 (varredura) e D6 (runbook/passos de deploy)
- Código/docs a varrer (pontos conhecidos): `apps/pipeline/schemas/blocks.py` (L12/L53), `apps/pipeline/schemas/base.py` (L190), `apps/pipeline/tests/test_schemas.py` (docstrings residuais), `templates/intake/my_cases.html` (L14), `static/js/procedure_combobox.js` (L7), `static/css/app.css` (L808), `config/settings/base.py` (L56), `README.md` (L5/L182/L191), `docs/runbook-deploy-producao.md` (L58–59/L88), docstrings cosméticas em `apps/attachments/tests/{test_pipeline_integration,test_tasks,test_verification}.py` e `apps/pipeline/tests/{test_llm1,test_llm2,test_orchestrator}.py` e `apps/intake/tests/test_my_cases_filters.py`

## Requirements

- **R1** Nenhuma ocorrência obsoleta de contagem antiga. Comando de varredura (evidência antes/depois no relatório):
  ```bash
  grep -rnE "13 tipos|13 perfis|os 13|\(13\)|[×x] ?13|10 tipos|11 intervencion|26 user|28 template|28 prompt|29 template|29 prompt|29 nome|29 conte" \
    apps/ config/ templates/ static/ scripts/ README.md docs/ \
    --include="*.py" --include="*.html" --include="*.js" --include="*.css" --include="*.md"
  ```
  Ocorrências remanescentes precisam ser menções históricas legítimas (decisões arquivadas citando o passado), justificadas uma a uma no relatório.
- **R2** Runbook (`docs/runbook-deploy-producao.md`): passo 5 deixa de sugerir "no-op em release 'sem migrations'" — novo texto deixa claro que **releases que adicionam seeds exigem o passo 5 mesmo sem migrations** (esta change é o caso: 2 templates novos); a convenção do changelog (L58–59) passa a distinguir "sem migrations e sem seeds (deploy direto)" de "sem migrations, MAS com seeds".
- **R3** Mudanças limitadas a comentários/docstrings/help/texto de documentação — sem qualquer mudança de comportamento. Evidência: diff do slice contém apenas strings de documentação.
- **R4** Quality gate completo verde: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`.
- **R5** Verificação operacional (settings dev, exige PostgreSQL dev ativo): `seed_procedure_catalog` 2× ("14 perfis, 8 seções"); `seed_prompts` 2× idempotente (total 31 ativos estável); `uv run python manage.py check --settings=config.settings.dev` sem erros. Se o banco dev não estiver disponível, registrar a limitação e manter como evidência primária os testes de seed (que cobrem idempotência contra banco de teste).

## Expected blast radius

```yaml
expected_files:   # somente strings de documentação/texto — nada de comportamento
  - apps/pipeline/schemas/blocks.py
  - apps/pipeline/schemas/base.py
  - apps/pipeline/tests/test_schemas.py            # apenas docstrings residuais não tocadas no 001
  - templates/intake/my_cases.html
  - static/js/procedure_combobox.js
  - static/css/app.css
  - config/settings/base.py
  - README.md
  - docs/runbook-deploy-producao.md                # R2 — texto operacional (sem procedimento novo)
  - apps/attachments/tests/{test_pipeline_integration,test_tasks,test_verification}.py  # docstrings
  - apps/pipeline/tests/{test_llm1,test_llm2,test_orchestrator}.py                     # docstrings
  - apps/intake/tests/test_my_cases_filters.py                                        # docstrings
out_of_scope:
  - qualquer mudança semântica de código/template renderizado (strings de documentação apenas)
  - CHANGELOG.md e PROJECT_CONTEXT.md (arquivamento)
  - specs/proposal/design/tasks da change
```

## Failing-before plan

```text
1. Rodar o grep do R1 > /tmp/nip-grep-antes.txt — as ocorrências obsoletas listadas
   são a evidência do estado a corrigir.
2. Após a varredura, re-rodar e salvar /tmp/nip-grep-depois.txt (ambos anexados ao
   relatório; remanescentes justificados).
```

## Implementation constraints

- Somente strings de documentação (comentários, docstrings, `help_text` estático, texto de docs). Nada de refatoração "aproveitada".
- Não reescrever histórico de decisões: comentários que registram decisões passadas (ex.: "contrato do change 01/P2 = 13 tipos") são reeditados preservando o fato histórico, não apagados.
- README/runbook: texto apenas — sem inventar procedimento operacional novo (o passo 5 já existe; o ajuste é de clareza sobre quando é obrigatório).

## Validation

**Focused (worker):** grep antes/depois (R1/R3); leitura do diff.

**Project-required gates (objetivo do slice):** gate completo do `AGENTS.md` §2 (R4).

**Runtime/verification evidence:** outputs de `seed_procedure_catalog` ×2, `seed_prompts` ×2 (contagem de ativos antes/depois da 2ª execução) e `manage.py check` dev (R5).

## Acceptance criteria

- [ ] R1 grep limpo (ou remanescentes históricos justificados um a um)
- [ ] R2 runbook sem a indução a pular seeds; convenção do changelog distinguindo "com seeds"
- [ ] R3 diff restrito a documentação/texto
- [ ] R4 gate completo verde
- [ ] R5 seeds idempotentes (14/8 e 31) e `check` dev limpo (ou limitação registrada)
- [ ] Evidências grep antes/depois anexadas

## Escalation conditions

Parar como `BLOCKED_NEEDS_DECISION` se: alguma ocorrência estiver em string com significado semântico (validação/comparação) — indício de contrato não mapeado; o gate revelar falha atribuível ao slice 001; `seed_prompts` duplicar templates na 2ª execução (quebra de idempotência a escalar).

## Evidence report

`/tmp/hmd-slice-nip-002-report.md`: status, R1–R5 → evidência, arquivos alterados, greps antes/depois, comandos do gate e seeds com exit status, remanescentes históricos e justificativas, riscos. Não versionar o relatório.

## Worker handoff

```text
Implement only this approved slice, after slice 001 is accepted.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy in AGENTS.md; documentation-in-code/docs
only — no behavior change, no refactoring.
Run the full quality gate and the operational seed verification, then write the
evidence report.
Do not update tasks.md, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with BLOCKED_NEEDS_DECISION and evidence.
Otherwise finish with READY_FOR_REVIEW and REPORT_PATH=/tmp/hmd-slice-nip-002-report.md.
```
