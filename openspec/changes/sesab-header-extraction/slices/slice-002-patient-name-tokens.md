# Slice 002 — Nome do paciente do cabeçalho + tokenização end-to-end

## Contexto necessário

- Corpus real (dev): o nome completo do paciente está no `extracted_text`
  nas linhas de cabeçalho (L3/L87/L173), na linha de demografia
  `<NOME> - Idade: 79a. - Sexo: F - Raça/Cor: ...`, na linha ANTERIOR ao
  rótulo `Paciente:` sozinho. Hoje vai ao LLM EM CLARO (o
  `_PATIENT_NAME_FIELD_PATTERN` exige valor na mesma linha do rótulo e
  nunca casa no layout real).
- `apps/anonymization/deterministic.py`: `_PATIENT_NAME_FIELD_PATTERN`
  (~57), `extract_patient_name` (~93), `_capture_field_value`,
  `_FIELD_BREAK_LABELS`/`_fold`; `DeterministicExtraction` (~86) com
  `patient_name`/`birth_date`/candidatos CPF/CNS.
- `apps/anonymization/services.py`: `_deterministic_candidates` (~249)
  tokeniza TODAS as ocorrências de `extraction.patient_name`
  (`_find_folded_occurrences`) e do nascimento
  (`_find_date_occurrences`); `anonymize_case_text` (~176) persiste o
  linkage («SEMPRE sobrescrito pela extração» — contrato R4 vigente,
  inalterado).
- Nome social: rótulo `Nome Social:` existe no cabeçalho (vazio no corpus
  real; pode estar preenchido). Não é o campo de captura do nome civil
  (decisão do dono).
- Guard `_assert_tokens_only` (llm-pipeline) intocado.
- Benchmark: `manage.py anonymization_benchmark --corpus ...` (JSONL
  `{"text":..., "expected":[...]}`); corpus REAL é aceite operacional
  local.

## Goal

O nome do paciente é extraído do layout real do cabeçalho e tokenizado em
TODAS as ocorrências antes de qualquer LLM; nome social presente vira
candidato PESSOA próprio; o linkage do caso popula.

## Deliverables

### R1 — Âncora do cabeçalho padrão em `extract_patient_name`

- Antes dos patterns vigentes (que permanecem para outros layouts):
  importar do `apps/intake/pdf_utils.py` (fonte única, slice 001 R1) o
  pattern da **linha de demografia canônica** (`Idade`+`Sexo`+`Raça/Cor`
  NA MESMA LINHA, na ordem) e procurar linha que casa E cuja linha
  SEGUINTE é `Paciente:` sozinho (fold, dois-pontos opcionais); nome =
  prefixo da linha até ` - Idade:` (strip); validação ≥ 2 palavras com
  letras. Primeira válida vence. Menção clínica órfã de idade SEM os três
  marcadores nunca casa (âncora tripla).

### R2 — Nome social como candidato PESSOA (sem linkage, captura conservadora)

- `DeterministicExtraction` ganha `social_name: str | None` (default
  `None`). Captura APENAS o valor na MESMA linha após `Nome Social:`
  (quando o rótulo tem valor à direita), validado: ≥ 1 palavra alfabética
  (≥ 2 letras, sem dígitos/símbolos) e ≠ nome civil (fold). **SEM fallback
  de linha anterior** (especulativo; rótulo sozinho/vazio → `None`).
- `_deterministic_candidates`: `social_name` vira candidato PESSOA (todas
  as ocorrências folded) — token próprio distinto (valores distintos).
- Limitação documentada: valor desalinhado (linha anterior/seguinte) fica
  NÃO-capturado — aceite operacional com PDF real é o detector.

### R3 — Linkage e anonimização end-to-end (mecanismo existente)

- Sem mudança de contrato: `anonymize_case_text` persiste
  `patient_name`/`patient_birth_date` pela extração (R4 vigente); com R1 o
  campo popula no layout real.
- Nenhuma mudança em `anonymize_text`/`seed_map`/merge — o candidato
  determinístico do nome já existente cobre as ocorrências por página.

### R4 — Benchmark e testes (RED→GREEN)

- Fixture `benchmark_corpus.jsonl`: entrada sintética com o layout real
  (nome desalinhado + demografia + `Paciente:` sozinho + páginas
  repetidas), expected PESSOA 100%; entrada com nome social preenchido;
  entrada com nome social vazio.
- Novos (RED): extração do layout real (uma e múltiplas páginas); nome
  ausente → `None` (rótulos sozinhos não geram); **adversariais**:
  «…Idade: 79a.» clínica órfã (sem Sexo+Raça/Cor na mesma linha, mesmo
  seguida de «Paciente:») NÃO gera nome; nome social mesma-linha
  preenchido/vazio-após-texto-clínico (rótulo sozinho não captura a linha
  anterior); tokenização de TODAS as ocorrências (3 páginas → mesmo
  `<PESSOA_1>`); nome social com token próprio; texto anonimizado SEM o
  nome (varredura folded); linkage persistido (`patient_name` no caso);
  nascimento não extraído do SESAB real (`patient_birth_date` None) e
  pattern vigente intacto (regressão: layout com `Nascimento:` persiste a
  data).
- Aceite operacional (local, documentado no slice — NÃO no CI): corpus
  REAL do piloto (dev) → recall PESSOA 100%, `patient_name` do caso real
  populado, `anonymized_text` sem o nome.
- Bateria: `TEST_DB_PORT=55435 uv run pytest -q apps/anonymization` +
  suíte cheia + benchmark com a fixture.

## Gates para o reviewer (2 linhas)

1. `apps/anonymization/deterministic.py`: a âncora nova exige a linha de
   demografia canônica COMPLETA (os três marcadores `Idade`+`Sexo`+
   `Raça/Cor` na mesma linha, pattern importado de `pdf_utils`) E o rótulo
   `Paciente:` sozinho na linha seguinte E prefixo com ≥ 2 palavras — teste
   adversarial provando que «…Idade: 79a.» clínica órfã (sem Sexo/Raça na
   mesma linha, mesmo seguida de «Paciente:») NÃO gera nome NEM metadados.
2. `social_name` NÃO aparece em nenhum write de campo do `Case` (só
   candidatos PESSOA no núcleo, captura restrita à mesma linha do rótulo —
   sem fallback) — sweep de writers de `patient_name` permanece
   `anonymize_case_text` (writer único).

## Out of scope

- Metadados idade/sexo/raça/dias (slice 001).
- UI e filas (changes seguintes); NER (opt-in desligado); guard
  `_assert_tokens_only` (intocado); captura manual no intake (descartada —
  o relatório tem tudo).
