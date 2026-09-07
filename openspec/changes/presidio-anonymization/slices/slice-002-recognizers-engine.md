# Slice 002: Recognizers BR + engine Presidio singleton

## Objetivo

Recognizers brasileiros com validação por checksum (`BR_CPF`, `BR_CNS`, `BR_CRM`) e o engine Presidio singleton (spaCy `pt_core_news_lg` via YAML, registry com predefined + BR, threshold por env). Primeiro slice com as dependências pesadas (presidio/spacy/modelo pinados no lockfile).

## Contexto necessário (contexto zero)

- Fonte autoritativa: `temp/research/presidio-django-ptbr.md` §3 (versões/pins), §5 (YAML do NlpEngine + fluxo analyzer/anonymizer + `score_threshold=0.45`), §6 (esboço COMPLETO dos recognizers com checksum — CPF soma verificável, CNS soma ponderada 15..1 % 11, CRM padrão; **adaptar, não copiar cego**), §10 (singleton/worker/RSS), §12 (MIT; pinar versões).
- Slice 001 entregou `apps/anonymization` (deterministic puro).
- **Modelo spaCy**: wheel do `pt_core_news_lg` 3.8.0 travado por URL no `pyproject.toml` (instalação determinística no `uv sync`; ~541 MB — primeira sincronização lenta). `ANONYMIZATION_SPACY_MODEL` (default `pt_core_news_lg`) permite trocar.
- Testes de recognizer são **puros e rápidos** (não carregam o modelo); testes do engine carregam (marcar como lentos se necessário, mas rodam na suíte).

## Requisitos

- **R1** `pyproject.toml`/`uv.lock`: `presidio-analyzer==2.2.364`, `presidio-anonymizer==2.2.364`, `spacy==3.8.*` + wheel `pt_core_news_lg` (URL oficial dos releases do spaCy); **`requires-python = ">=3.13,<3.15"`** (Presidio declara `<3.15` — pesquisa §3; trava o salto acidental); `uv sync` resolve e importa.
- **R2** Validadores puros em `apps/anonymization/recognizers.py`: `valid_cpf` (checksum completo, rejeita repetidos), `valid_cns` (soma ponderada 15..1 múltipla de 11, rejeita repetidos) — funções públicas testáveis.
- **R3** `CpfRecognizer`/`CnsRecognizer` (PatternRecognizer + validação por valor casado — só resultado com checksum válido passa), `CrmRecognizer` (padrão CRM/UF/número), entidades `BR_CPF`/`BR_CNS`/`BR_CRM`, `supported_language="pt"`, contextos de reforço da pesquisa.
- **R4** `apps/anonymization/engine.py`: `config/presidio-nlp-pt.yaml` (mapping PER/LOC/ORG/DATE→Presidio, low-confidence da pesquisa) + `get_anonymization_engine()` singleton (`lru_cache(maxsize=1)`): NlpEngineProvider → RecognizerRegistry (predefined + BR) → `AnalyzerEngine(supported_languages=["pt"])` + `AnonymizerEngine`; `score_threshold` de `ANONYMIZATION_SCORE_THRESHOLD` (default 0.45) exposto como constante/setting lida na análise.
- **R5** Testes de recognizer: CPF válido gerado algoritmicamente detectado; CPF com dígito verificador errado **não** detectado; idem CNS; CRM nos formatos com/sem UF; regex não casa dígitos vizinhos (lookarounds).
- **R6** Testes de engine (carregam o modelo): analyzer detecta PERSON em texto pt sintético com nome; detecta BR_CPF de CPF válido gerado; threshold respeitado; singleton retorna a MESMA instância em chamadas repetidas.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `pyproject.toml`, `uv.lock` | `uv run python -c "import presidio_analyzer, presidio_anonymizer, spacy, pt_core_news_lg"` |
| R2 | `apps/anonymization/recognizers.py` | `test_recognizers.py::test_valid_cpf_generated`, `::test_invalid_cpf_rejected`, `::test_valid_cns_generated`, `::test_invalid_cns_rejected` |
| R3 | `apps/anonymization/recognizers.py` | `::test_cpf_recognizer_detects_valid_only`, `::test_crm_formats` |
| R4 | `apps/anonymization/engine.py`, `config/presidio-nlp-pt.yaml` | `test_engine.py::test_singleton_same_instance`, `::test_detects_person_pt`, `::test_detects_generated_cpf` |
| R5/R6 | `apps/anonymization/tests/{test_recognizers,test_engine}.py` | `uv run pytest apps/anonymization/tests/` |

## RED

- Comando: `uv run pytest apps/anonymization/tests/test_recognizers.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.anonymization.recognizers'`.

## GREEN / verificação local

- `uv run pytest apps/anonymization/tests/` — exit 0 (recognizers + engine + deterministic)
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0 (overrides mypy para presidio/spacy sem py.typed, se necessário — registrar)
- `uv run python -c "from apps.anonymization.engine import get_anonymization_engine; e=get_anonymization_engine(); print(type(e).__name__)"` — carrega sem erro

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/anonymization/recognizers.py
  - apps/anonymization/engine.py
  - apps/anonymization/tests/{test_recognizers,test_engine}.py
  - config/presidio-nlp-pt.yaml
  - config/settings/base.py        # ANONYMIZATION_SCORE_THRESHOLD / ANONYMIZATION_SPACY_MODEL
  - pyproject.toml
  - uv.lock
  - .env.example
allowed_incidental_files:
  - apps/anonymization/tests/conftest.py (gerador de CPF/CNS válidos como fixture)
out_of_scope:
  - serviço de anonimização do caso/campos (003); worker/task (004); benchmark (005)
  - nenhum import do engine em código de request/web (só worker/testes)
```

Escale ao parent se: a API do presidio 2.2.364 divergir materialmente da pesquisa (Pattern/RecognizerRegistry/NlpEngineProvider); o wheel do modelo não resolver no lockfile.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz
- [ ] CPF/CNS com checksum — valor inválido NUNCA detectado
- [ ] Singleton comprovado (uma instância por processo)
- [ ] Gate parcial do slice verde
