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

- Antes dos patterns vigentes (que permanecem para outros layouts): procurar
  linha com `Idade:\s*(\d+)\s*a\.?` cuja linha SEGUINTE seja `Paciente:`
  (sozinho, fold); nome = prefixo da linha até ` - Idade:` (strip);
  validação ≥ 2 palavras com letras. Primeira válida vence.

### R2 — Nome social como candidato PESSOA (sem linkage)

- `DeterministicExtraction` ganha `social_name: str | None` (default
  `None`). Captura: valor na mesma linha após `Nome Social:` quando o
  rótulo não está sozinho; senão linha ANTERIOR ao rótulo sozinho
  (simetria do desalinhamento), validada ≥ 1 palavra alfabética e ≠ nome
  civil (fold). Ausente/vazio → `None`.
- `_deterministic_candidates`: `social_name` vira candidato PESSOA (todas
  as ocorrências folded) — token próprio distinto (valores distintos).

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
  ausente → `None` (rótulos sozinhos não geram); nome social mesma
  linha/linha anterior/vazio; tokenização de TODAS as ocorrências (3
  páginas → mesmo `<PESSOA_1>`); nome social com token próprio; texto
  anonimizado SEM o nome (varredura folded); linkage persistido
  (`patient_name` no caso); nascimento não extraído do SESAB real
  (`patient_birth_date` None) e pattern vigente intacto (regressão:
  layout com `Nascimento:` persiste a data).
- Aceite operacional (local, documentado no slice — NÃO no CI): corpus
  REAL do piloto (dev) → recall PESSOA 100%, `patient_name` do caso real
  populado, `anonymized_text` sem o nome.
- Bateria: `TEST_DB_PORT=55435 uv run pytest -q apps/anonymization` +
  suíte cheia + benchmark com a fixture.

## Gates para o reviewer (2 linhas)

1. `apps/anonymization/deterministic.py`: a âncora nova só produz nome com
   as DUAS validações (linha seguinte = `Paciente:` sozinho E prefixo com
   ≥ 2 palavras) — teste provando que linha de demografia ÓRFÃ (sem o
   rótulo na linha seguinte, ex. menção de idade em texto clínico) NÃO
   gera nome.
2. `social_name` NÃO aparece em nenhum write de campo do `Case` (só
   candidatos PESSOA no núcleo) — sweep de writers de `patient_name`
   permanece `anonymize_case_text` (writer único).

## Out of scope

- Metadados idade/sexo/raça/dias (slice 001).
- UI e filas (changes seguintes); NER (opt-in desligado); guard
  `_assert_tokens_only` (intocado); captura manual no intake (descartada —
  o relatório tem tudo).
