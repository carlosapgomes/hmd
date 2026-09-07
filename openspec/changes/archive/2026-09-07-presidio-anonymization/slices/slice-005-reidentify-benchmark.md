# Slice 005: Re-identificação + harness de benchmark

## Objetivo

Fechar o ciclo: `reidentify_text` (tokens → valores reais, roundtrip fiel) e o comando `anonymization_benchmark` (corpus JSONL com recall por entidade, p50/p95, varredura zero-PII, exit≠0 abaixo do mínimo) com corpus sintético versionado que roda na suíte.

## Contexto necessário (contexto zero)

- Slices 001–004 entregues: deterministic, engine, serviço com mapa 1:1, worker/FSM.
- Dono/plano: §6.5 (re-identificação na renderização para doctor/manager/admin — **mecânica aqui, controle de acesso no presenter do 07**) e §6.7 (benchmark local como critério de aceite; roadmap: "harness de benchmark").
- Pesquisa §9 (roundtrip/fidelidade), §13 (recall > precisão; avaliação local), §14 (critério de aceite: recall por entidade, p95, taxa bloqueada).
- Design: `../design.md` D8 (reidentify ordenado por tamanho), D9 (benchmark/corpus sintético/min recall). Spec: Requirements "Re-identificação controlada" (1 cenário) e "Benchmark como critério de aceite" (2 cenários).

## Requisitos

- **R1** `reidentify_text(case, text) -> str`: **regex única** com alternation de todos os tokens + callback pelo `pseudonym_map` (uma passada — imune a cascata e a `<PESSOA_1>` casar dentro de `<PESSOA_10>`); mapa vazio → texto inalterado; função pura sobre (mapa, texto); testes com valor original que literalmente contém um token.
- **R2** `manage.py anonymization_benchmark --corpus <path> [--min-recall X]`: lê JSONL `{"text": ..., "expected": [{"value": ..., "entity_type": ...}]}`; para cada entrada roda o **núcleo puro `anonymize_text`** (slice 003 — sem caso/DB), mede: acerto por entidade esperada (valor ausente do output), contagens por tipo detectado, latência por documento; agrega recall por `entity_type`, p50/p95, entidades totais; **varredura zero-PII** (regex CPF/CNS com checksum no output = falha); **pico de RSS do processo** (`resource.getrusage`; limite opcional `ANONYMIZATION_BENCHMARK_MAX_RSS_MB`, default desligado) e **documentos bloqueados** (erro de anonimização durante o benchmark — qualquer bloqueio reprova); imprime relatório; exit 0 somente se todo tipo ≥ `--min-recall` (default 0.90) E zero-PII limpo E zero bloqueios.
- **R3** Corpus sintético versionado: `apps/anonymization/tests/fixtures/benchmark_corpus.jsonl` — ≥ 8 entradas com dados fabricados (nomes fictícios, CPF/CNS válidos **gerados algoritmicamente**, datas, CRM fictício, textos de estilo clínico/regulatório com os labels SESAB); sem PII real.
- **R4** Testes: roundtrip — anonimiza texto sintético via serviço e `reidentify_text` reproduz integralmente os valores originais (comparação campo a campo das substituições); benchmark sobre o corpus sintético → exit 0 com recall ≥ mínimo (roda via `call_command`); corpus adversário (entrada com entidade que o pipeline não pega por regex — ex. nome exótico sem span determinístico e NER falha) → exit ≠ 0 com relatório apontando o tipo reprovado; zero-PII violado (CPF válido injetado que passa sem detecção) → exit ≠ 0.
- **R5** `.env.example` documenta `ANONYMIZATION_BENCHMARK_MIN_RECALL`; README ganha seção curta de uso operacional (corpus real pré-produção).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/anonymization/reidentify.py` | `test_reidentify.py::test_roundtrip_full`, `::test_empty_map_unchanged`, `::test_longest_token_first` |
| R2 | `apps/anonymization/management/commands/anonymization_benchmark.py` | `test_benchmark.py::test_synthetic_corpus_passes`, `::test_adversarial_corpus_fails`, `::test_zero_pii_violation_fails`, `::test_blocked_document_fails` |
| R3 | `apps/anonymization/tests/fixtures/benchmark_corpus.jsonl` | `test_benchmark.py::test_synthetic_corpus_passes` (consome) |
| R4 | `apps/anonymization/tests/{test_reidentify,test_benchmark}.py` | `uv run pytest apps/anonymization/tests/test_reidentify.py apps/anonymization/tests/test_benchmark.py` |
| R5 | `.env.example`, `README.md` | `rg -n "ANONYMIZATION_BENCHMARK_MIN_RECALL" .env.example` |

## RED

- Comando: `uv run pytest apps/anonymization/tests/test_reidentify.py`
- Falha esperada: `ModuleNotFoundError: apps.anonymization.reidentify`.

## GREEN / verificação local

- `uv run pytest apps/anonymization/tests/test_reidentify.py apps/anonymization/tests/test_benchmark.py` — exit 0
- `uv run pytest apps/anonymization/tests/` — exit 0
- `uv run python manage.py anonymization_benchmark --corpus apps/anonymization/tests/fixtures/benchmark_corpus.jsonl --settings=config.settings.dev` — exit 0 com relatório
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/anonymization/reidentify.py
  - apps/anonymization/management/commands/anonymization_benchmark.py
  - apps/anonymization/tests/{test_reidentify,test_benchmark}.py
  - apps/anonymization/tests/fixtures/benchmark_corpus.jsonl
  - config/settings/base.py        # ANONYMIZATION_BENCHMARK_MIN_RECALL
  - .env.example
  - README.md
allowed_incidental_files: []
out_of_scope:
  - controle de acesso à re-identificação (07); UI; corpus real (operacional)
  - recognizers novos para passar casos adversários (calibração é pós-benchmark real)
```

Escale ao parent se: o corpus sintético não atingir o recall mínimo com o pipeline atual (indica gap real do engine — decidir entre calibrar threshold/recognizers agora ou registrar risco).

## Critérios de aceitação

- [ ] R1–R5 comprovados pelos comandos da matriz (cenários de re-identificação e benchmark da spec)
- [ ] Roundtrip fiel (mapa 1:1)
- [ ] Benchmark reprova abaixo do mínimo e em violação zero-PII (exit ≠ 0)
- [ ] Corpus sintético sem PII real, versionado, passa no CI
- [ ] Gate parcial do slice verde
