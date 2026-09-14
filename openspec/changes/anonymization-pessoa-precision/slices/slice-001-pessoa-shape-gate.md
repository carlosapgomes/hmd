# Slice 001 — Portão de forma PESSOA + corpus do incidente

## Contexto necessário

- Núcleo: `apps/anonymization/services.py::anonymize_text` — ordem atual:
  `run_deterministic_extraction` → `_deterministic_candidates` → loop dos
  `analyzer_results` (NER → `SpanCandidate(..., deterministic=False)` com
  categoria via `NER_ENTITY_TYPE_TO_CATEGORY`) → `merge_span_candidates` →
  `PseudonymOperator`. O portão entra ANTES do merge, só para
  `category == PESSOA and deterministic is False`.
- Categorias: `PESSOA` importado onde os candidates são criados (ver o enum
  em `apps/anonymization/deterministic.py`/`operators.py`).
- Corpus do incidente (vocabulário clínico genérico, SEM dado de paciente):
  "Afebril", "Diurese", "DORSO", "DOENÇA", "Elicris" (1 token);
  "VENT.:Ar Ambiente" (dois-pontos); "PAS:100\nPAD:70" (dígitos+quebra);
  "DOENÇA ARTERIAL PERIFÉRICA" (all-caps multi-token sem assinatura — nota 2
  do Eon; o relatório do incidente É de art_perif).
- Diagnóstico de guard existente (d6845cc): `logger.error` privacy-safe em
  `_assert_tokens_only` — NÃO mexer.
- `anonymization_report` já existe (spec: auditável) — adicionar só
  CONTADORES de rejeição.

## Goal

Vocabulário clínico nunca vira PESSOA; nomes reais (Title Case, assinatura,
paciente determinístico) seguem tokenizados; guard intocado.

## Deliverables

### R1 — `apps/anonymization/pessoa_shape.py` (novo, funções puras)

- `is_plausible_person(candidate_text, *, context, offset, offset_end) -> bool`
  implementando as 4 regras do design D1 (≥2 tokens; estritamente
  alfabético `^[A-Za-zÀ-ÿ]+(?:[-'’][A-Za-zÀ-ÿ]+)*$`; evidência de caixa
  Title Case OU contexto de assinatura ±80 chars com
  `CRM|CRF|COREN|DRA?\.\s|ENF\.|ASSIN` case-insensitive; stoplist por frase
  normalizada — lista mínima observada no design).
- Sem I/O, sem settings, sem dependência de DB — puro e unit-testável.

### R2 — Aplicação no núcleo (`services.py`)

- No loop dos `analyzer_results`: candidato NER→PESSOA rejeitado não vira
  `SpanCandidate`. Demais categorias e determinísticos inalterados.
- `anonymization_report` ganha contadores `pessoa_rejected_by_shape/caça/stoplist`
  (nomes exatos a definir na implementação; SÓ contagens).

### R3 — Testes (`apps/anonymization/tests/test_pessoa_shape.py` + integração)

- Unit do portão: tabela verdade com TODOS os casos do corpus do incidente
  (negativos) + positivos do design D3 (Title Case com partícula,
  hífen/apóstrofo, assinatura all-caps) + limites (janela de 80 chars
  exatamente no bordo; partícula "da/de/dos/e" minúscula não mata Title
  Case).
- Integração via `anonymize_text` (núcleo): texto sintético com os
  fragmentos do incidente E um nome Title Case → mapa SEM os clínicos, COM o
  nome; texto all-caps com assinatura CRM → nome tokenizado; paciente
  determinístico em all-caps sem assinatura → seed cobre (invariante).
- Não-vacuidade: mutação (portão desligado) faz os negativos falharem —
  demonstre com RED na implementação (testes primeiro).
- Regressão: suíte de anonymization E pipeline verdes sem edição.

### R4 — Docs

- `PROJECT_CONTEXT.md` ou README (1 parágrafo): porta do portão + como
  operar a stoplist (adicionar termo observado + teste) — sem segredo
  clínico além do vocabulário genérico já citado.

## Out of Scope

- Guard `_assert_tokens_only` (intocável); threshold/modelo spaCy; OCR de
  anexos; prompts; UI; migrations; `ANONYMIZATION_*` settings.

## Matriz requisito → arquivo → teste

| Requisito/spec | Código | Teste |
|---|---|---|
| 1 token clínico rejeitado | pessoa_shape.py | unit (corpus) |
| dígitos/pontuação rejeitados | pessoa_shape.py | unit (corpus) |
| all-caps sem assinatura rejeitado | pessoa_shape.py | unit + integração |
| Title Case tokenizado | pessoa_shape.py | unit + integração |
| assinatura all-caps tokenizado | pessoa_shape.py | unit + integração |
| paciente determinístico intacto | services.py (portão não alcança) | integração (seed all-caps) |
| stoplist por frase | pessoa_shape.py | unit |
| contadores no report | services.py | integração (presença das chaves) |

## Verification (RED → GREEN, mesmo comando)

```bash
TEST_DB_PORT=55435 uv run pytest apps/anonymization -q
# GREEN total + higiene:
TEST_DB_PORT=55435 uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

## Expected files

- apps/anonymization/pessoa_shape.py (novo)
- apps/anonymization/services.py
- apps/anonymization/tests/test_pessoa_shape.py (novo)
- README.md (ou PROJECT_CONTEXT.md — 1 parágrafo de operação da stoplist)

- allowed incidental files: NENHUM

## Acceptance criteria

- Corpus do incidente 100% rejeitado como PESSOA; positivos 100% tokenizados.
- Paciente determinístico e demais categorias inalterados (suíte de
  regressão verde sem edição).
- Guard intocado; report só ganha contadores.
- Suíte completa verde; ruff/format/mypy limpos.

## Deviations / learnings

- (preenchido na execução)
