# Slice 003: PromptTemplate versionado + seeds + montagem

## Objetivo

`apps/llm`: model `PromptTemplate` (unique `(name,version)`, 1 ativo por nome), seeds idempotentes (`llm1.system`/`llm2.system` neutros + `proc.<type>.llm1.user`/`proc.<type>.llm2.user` ×13) e a montagem do prompt do caso (system neutro + user composto pelos blocos dos tipos + payload do estágio).

## Contexto necessário (contexto zero)

- Referência: `/projects/dev/ats-web/apps/llm/models.py::PromptTemplate` (unique_together `(name, version)`; 1 ativo por nome via constraint parcial) e `apps/llm/management/commands/seed_prompts.py` (SOMENTE-LEITURA — adaptar nomes/conteúdo p/ HMD).
- Plano §7 (**emenda aprovada em review 2026-09-07, registrada no plano**): systems neutros compartilhados (`llm1.system`/`llm2.system`) porque a composição união exige UMA chamada por estágio; users por tipo (`proc.<type>.llm1.user`/`proc.<type>.llm2.user` × 13 = 28 templates). Divergência com o ats-web registrada: lá o 1-ativo-por-nome é garantia de app (`clean/save`), aqui vira constraint parcial no banco (melhoria).
- Catálogo: labels dos 13 tipos em `apps/cases/procedure_catalog.py` (usar nos textos).
- Conteúdo dos prompts: pt-BR, papel/guardas (JSON estrito, idioma pt-BR, evidence obrigatória, nunca inventar, usar tokens `<PESSOA_1>` etc. quando citar pessoas, status tri-state). Conteúdo clínico por tipo deriva de `temp/parametrosHMD.md` (seção do tipo).

## Requisitos

- **R1** `PromptTemplate`: `name CharField unique=False`, `version PositiveIntegerField`, `content TextField`, `is_active bool`; unique `(name, version)`; **no máximo 1 ativo por nome** (constraint parcial `UniqueConstraint(condition=Q(is_active=True), fields=["name"])`); timestamps; migration; helper `get_active_prompt(name)` (falha explícita se nenhum ativo).
- **R2** `seed_prompts` idempotente: cria (se ausente) e ativa `llm1.system`, `llm2.system` e `proc.<type>.llm1.user`/`proc.<type>.llm2.user` para os 13 tipos (28 templates); reexecutar não duplica nem sobrescreve conteúdo editado (só ativa se não houver ativo); resumo com contagem.
- **R3** `build_case_prompts(stage ∈ {llm1, llm2}, types, *, extra_context="") -> list[ChatMessage]`: system = `<stage>.system` ativo; user = concatenação dos `proc.<type>.<stage>.user` ativos (ordem canônica do catálogo) + placeholders `{texto_anonimizado}`/`{visao_estruturada}`/`{policy}`/`{prior_case}` conforme estágio (placeholders substituídos pelo chamador; montagem não vê conteúdo do caso). Tipo sem prompt ativo → erro nomeando o tipo.
- **R4** Event/auditoria helpers: `prompt_usage(stage, types)` → `{names+versions}` para payloads de eventos (enxuto).
- **R5** Testes: constraint de 1 ativo por nome (segundo ativo viola); seeds idempotentes (2× sem duplicar; 28 presentes; não sobrescreve edição); `build_case_prompts` com 1 e 3 tipos (system neutro único, users na ordem canônica, placeholders presentes); tipo sem ativo → erro nomeado; `get_active_prompt` sem ativo → erro.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/llm/{models,migrations/0001_initial}.py` | `test_prompts.py::test_single_active_per_name` |
| R2 | `apps/llm/management/commands/seed_prompts.py`, `apps/llm/prompts_seed.py` (conteúdo) | `::test_seed_idempotent`, `::test_seed_count_28`, `::test_seed_preserves_edits` |
| R3 | `apps/llm/services.py` (build_case_prompts) | `::test_build_single_and_multi_type`, `::test_missing_active_named_error` |
| R4 | `apps/llm/services.py` | `::test_prompt_usage_payload` |
| R5 | `apps/llm/tests/test_prompts.py` | `uv run pytest apps/llm/tests/test_prompts.py` |

## RED

- Comando: `uv run pytest apps/llm/tests/test_prompts.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.llm'`.

## GREEN / verificação local

- `uv run pytest apps/llm/tests/test_prompts.py` — exit 0
- `uv run python manage.py seed_prompts --settings=config.settings.dev` (contra compose dev ou test) — resumo + 2× idempotente
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0
- `uv run python manage.py makemigrations --check --dry-run` — sem drift

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/llm/{__init__,apps,models,services}.py
  - apps/llm/prompts_seed.py
  - apps/llm/migrations/0001_initial.py
  - apps/llm/management/{__init__,commands/{__init__,seed_prompts}}.py
  - apps/llm/tests/{__init__,test_prompts}.py
  - config/settings/base.py        # INSTALLED_APPS += apps.llm
out_of_scope:
  - chamadas LLM (004/006); schemas (002 já entregues); policy (005); UI/admin de prompts (change 11/admin_ui)
```

Escale ao parent se: o conteúdo de algum prompt exigir decisão clínica não coberta pelo parametrosHMD.

## Critérios de aceitação

- [ ] R1–R5 comprovados; 28 seeds idempotentes
- [ ] Montagem não vê conteúdo do caso (só placeholders)
- [ ] Gate parcial do slice verde
