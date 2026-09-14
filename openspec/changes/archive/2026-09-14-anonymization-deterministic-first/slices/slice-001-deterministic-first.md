# Slice 001 — Setting determinístico-first + seed do prior-case + pino do incidente

## Contexto necessário

- `anonymize_text` (`apps/anonymization/services.py:83`): ordem hoje =
  `run_deterministic_extraction` → early-return p/ texto vazio →
  `get_anonymization_engine()` → `engine.analyzer.analyze(text, "pt",
  score_threshold=score_threshold())` → `_deterministic_candidates` + loop
  NER (`SpanCandidate(..., deterministic=False)`) → `merge_span_candidates`
  → `PseudonymOperator`. O gate do setting envolve SOMENTE o bloco
  engine/analyze + loop NER.
- `get_anonymization_engine` é singleton lru_cache (`engine.py`) — com NER
  off ele NÃO pode ser chamado (senão carrega spaCy lg à toa).
- Prior-case: `_anonymized_reason(reason)` (`prior_case.py:141`) recebe o
  motivo de `_build_summary` (que tem `row.case` via select_related, `:83`) —
  passar `seed_map=row.case.pseudonym_map` (ajustar a passada do argumento
  pelo caller).
- Test settings: `config/settings/test.py` já pina envs (UNIT_LABELS,
  OPENROUTER_*) — adicionar `ANONYMIZATION_USE_NER = True`.
- Fragmentos do incidente (vocabulário clínico GENÉRICO, sem dado de
  paciente): PESSOA-fantasma "Afebril", "Diurese", "DORSO", "DOENÇA",
  "Elicris", "Código", "Dias Unid", "ARTERIOGRAFIA", "Prof", "Reg";
  LOCAL-fantasma "Macro", "Nordeste\nMotivo da Solicitação", "D0",
  "Murmúrio", "MID", "PAS:100\nPAD:80", "CREMEB", "Informado", "MG",
  "FR:18", "FC:77\nFR:18", "FC:74\nFR:18\nPAS:120", "Mot", "Solicit".
- Guard: `_assert_tokens_only` (`apps/pipeline/llm1_service.py`) — importar e
  chamar direto no teste E2E com um `Case` de fábrica de testes.
- Benchmark: `management/commands/anonymization_benchmark.py` chama
  `services.anonymize_text(entry.text)`; teste de CI em
  `tests/test_benchmark.py` (usa `_ADVERSARIAL_NAME`).

## Goal

Mapa determinístico-only por default; prior-case semeado; incidente pinnado
E2E; reversibilidade por env documentada e testada.

## Deliverables

### R1 — Setting + gate (base.py, services.py)

- `ANONYMIZATION_USE_NER` em `config/settings/base.py` (default False,
  parse igual aos `*_RUN_TASKS_INLINE`).
- `config/settings/test.py`: `ANONYMIZATION_USE_NER = True` (pin).
- `anonymize_text`: `if settings.ANONYMIZATION_USE_NER:` envolve
  engine/analyze/loop-NER (engine não construído quando off);
  `_anonymization_report` ganha `ner_enabled` truthful (D6; com NER off
  omite `model`/versões Presidio).

### R2 — Prior-case semeado (`apps/pipeline/prior_case.py`)

- `_anonymized_reason` passa `seed_map=<mapa do caso anterior>` (conferir
  nome do atributo/objeto no código real).
- Teste: motivo citando o nome do paciente → texto ao LLM2 com o token do
  caso (sem rótulo no motivo).

### R3 — Testes

- Unit gate: com `override_settings(False)` e monkeypatch de
  `get_anonymization_engine` que **levanta se chamado** → anonymize_text não
  constrói engine; mapa/report só determinísticos (texto com rótulos).
- Reversibilidade: `override_settings(True)` + `_stub_engine` existente →
  candidatos NER somados (comportamento atual preservado).
- E2E incidente (D4 do design): texto sintético = cabeçalho SESAB com
  paciente (Nome/CNS/Nascimento/nº ocorrência) + corpo com TODOS os
  fragmentos listados no Contexto; `override(False)` → mapa só
  determinístico; artefato JSON representativo (contendo "profissional",
  "Registro", "Macro", "informado") passa em `_assert_tokens_only`; sem o
  change (RED), o mesmo teste com stub NER devolvendo os fantasmais falha.
- Benchmark: os DOIS testes de corpus (`test_synthetic_corpus_passes` —
  que EXIGE NER pelo CRM do corpus — e o adversarial) ganham
  `override_settings(ANONYMIZATION_USE_NER=True)` explícito (o pin de
  test.py já cobre; o override documenta a intenção).

### R4 — Compose + docs

- `docker-compose.prod.yml` E `docker-compose.dev.yml`:
  `ANONYMIZATION_USE_NER: ${ANONYMIZATION_USE_NER:-false}` em
  `worker-anonymization` E `worker-attachments` (sem isso a env do host não
  alcança os containers que anonimizam).
- `.env.example`: seção do setting (`#ANONYMIZATION_USE_NER=true` + nota de
  reativação com benchmark p/ calibrar; com NER off o corpus enviado falha
  no recall de CRM — o CRM é exclusivo do recognizer NER).
- `README.md`: parágrafo da decisão (terceiros = postura ats-web; paciente
  tokenizado ponta a ponta; reversível; mem do worker cai com NER off).
- Docstring do `anonymization_benchmark.py`: registrar a semântica do
  setting (NER on = calibra a camada completa; off = baseline
  determinístico; corpus padrão exige NER p/ CRM).

## Out of Scope

- Guard `_assert_tokens_only` (intocável); extração determinística; código
  NER/recognizers/engine (ficam); compose (default já é o novo posture);
  manual (não menciona NER — verificar; se mencionar, 1 linha).

## Matriz requisito → arquivo → teste

| Requisito/spec | Código | Teste |
|---|---|---|
| default só-determinístico | base.py+services.py | unit gate + E2E incidente |
| engine não construído | services.py | unit (engine-raise) |
| todas ocorrências do paciente | (já existe `_deterministic_candidates`) | E2E (corpo repete nome) |
| reversível por env | services.py | override(True)+stub |
| prior-case semeado | prior_case.py | teste do motivo |
| guard passa c/ mapa limpo | — | E2E `_assert_tokens_only` |
| benchmark calibra NER | test_benchmark.py | override(True) no CI |

## Verification (RED → GREEN, mesmo comando)

```bash
TEST_DB_PORT=55435 uv run pytest apps/anonymization apps/pipeline -q
# GREEN total + higiene:
TEST_DB_PORT=55435 uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

## Expected files

- config/settings/base.py
- config/settings/test.py
- apps/anonymization/services.py
- apps/pipeline/prior_case.py
- apps/pipeline/tests/test_prior_case.py
- apps/pipeline/tests/test_llm2.py            # fake 1-arg → aceitar seed_map (P0-2)
- apps/doctor/tests/test_detail.py            # idem (_fake_anonymize)
- apps/anonymization/tests/test_deterministic_first.py (novo: gate+E2E incidente)
- apps/anonymization/tests/test_benchmark.py (override explícito nos 2 corpus)
- apps/anonymization/management/commands/anonymization_benchmark.py (docstring)
- docker-compose.prod.yml
- docker-compose.dev.yml
- .env.example
- README.md

NOTA (P0-2): os fakes de `anonymize_text` em test_llm2.py:~480 e
doctor/test_detail.py:~455 assinam `(text)` — alargar para aceitar
`seed_map=None` (ou `**kwargs`) mantendo os asserts posicionais
existentes; NÃO reescrever os testes.

- allowed incidental files: NENHUM

## Acceptance criteria

- Default False: mapa de caso real-shaped SÓ determinístico; engine nunca
  construído (teste do raise).
- E2E do incidente verde (guard passa) e demonstrável RED pré-change.
- Prior-case semeado; reversibilidade NER testada; benchmark CI explícito.
- Suíte completa verde; ruff/format/mypy limpos.

## Deviations / learnings

- (execução) ESCALAMENTO aprovado pelo supervisor (opção B) — semeadura
  determinística dos valores do caso: além de passar `seed_map`, o núcleo
  (`anonymize_text`) passou a localizar as ocorrências dos VALORES SEMEADOS no
  texto como candidatos determinísticos (reuso do contrato de
  `_deterministic_candidates`: chave canônica por categoria, boundary-aware,
  sem inferência, valor exato do seed). Motivação: a semeadura sozinha só
  reutiliza tokens de spans JÁ detectados — com o NER desligado (default) o nome
  do paciente citado num motivo de negatura sem rótulo SESAB iria EM CLARO à
  OpenRouter. Fecha o residual do motivo E dos anexos que citam o paciente sem
  rótulo (política: identidade do paciente tokenizada ponta a ponta). Limites:
  nada além disso — guard/NER/extração por rótulos intocados; valor do seed
  ausente do texto não gera candidato nem entra no mapa.
- (execução) `_anonymization_report` truthful (D6) + payload do evento: com NER
  off o relatório omite `model`/`score_threshold`/versões do Presidio e o
  payload do `CASE_ANONYMIZATION_COMPLETED` acompanha (ganha `ner_enabled`) —
  nunca descreve engine que não rodou (e não estoura KeyError no worker com o
  default novo).
- (execução) testes do seed-scan no arquivo NOVO `test_deterministic_first.py`
  (a suíte de anexos não está no blast radius do slice — zero arquivos
  incidentais).
