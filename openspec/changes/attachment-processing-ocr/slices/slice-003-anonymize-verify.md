# Slice 003: Anonimização alinhada + verificação LLM de patient-match

## Objetivo

O texto extraído do anexo é anonimizado no espaço de tokens do caso e um LLM
de texto verifica — vendo APENAS tokens — se o documento é exame/relatório e
se corresponde ao paciente do caso; resultado `match|mismatch|unknown` +
resumo + evidence persistidos na row e em evento. Falha → `failed`.

## Contexto necessário

- Slices 001–002 entregues (row com `extracted_text`; task extrai e deixa
  `processing`; eventos `CASE_ATTACHMENT_*` existem — falta o PROCESSED).
- Design D4 (`openspec/changes/attachment-processing-ocr/design.md`) —
  convergência determinística de tokens (precedente
  `apps/pipeline/prior_case.py::_anonymized_reason`, que usa espaço próprio
  e converge com o mapa do caso para o mesmo paciente).
- `apps/anonymization/services.py::anonymize_text` (núcleo, mapa fresco por
  chamada) — o wrapper novo é ADITIVO, não altera `anonymize_case_text`.
- `apps/pipeline/llm.py::get_llm_client().complete(model, messages, *,
  json_schema)` — cliente do pipeline (injeção por monkeypatch nos testes,
  padrão das suítes do change 06); `settings.LLM1_MODEL`.
- Prompts versionados: `apps/llm/prompts_seed.py` (estrutura
  `PROMPT_SEED_CONTENTS` com nomes derivados de estágio/perfil) +
  `apps/llm/services.py::build_case_prompts` (referência de uso) +
  `manage.py seed_prompts` (idempotente — o seed novo entra na mesma
  estrutura com chave própria `ATTACHMENT_VERIFICATION`; **não** usar o
  mecanismo por-perfil).
- Assert de tokens recursivo: padrão das suítes do change 06 (grep
  `assert_no_real_data`/equivalente em `apps/pipeline/tests/`).
- `apps/cases/events.py` — acrescentar `CASE_ATTACHMENT_PROCESSED`.

## Requisitos verificáveis

- **R1** `apps/anonymization/services.py::anonymize_attachment_text(case,
  text)` (aditivo): roda o núcleo `anonymize_text`; devolve resultado com
  `anonymized_text` + `pseudonym_map` DO ANEXO; o mapa do CASO não é
  alterado (assert); convergência: entidade igual à do mapa do caso →
  mesmo token (teste com nome do paciente em ambos).
- **R2** `patient_token(case)` (apps/attachments; reverse lookup no mapa do
  caso: token cujo valor real == `case.patient_name`); sem
  paciente/token → verificação roda em modo sem-comparação e o resultado é
  `unknown` (documentado no prompt de saída).
- **R3** Prompt seed `ATTACHMENT_VERIFICATION` (29º conteúdo em
  `PROMPT_SEED_CONTENTS`; sistema+usuario com placeholders do texto
  anonimizado e do token; instruções: classificar se é exame/relatório,
  comparar o paciente do documento com o token dado; saída JSON com
  `patient_match`/`summary`/`evidence`) — `seed_prompts` idempotente
  (teste de idempotência como os 28 existentes).
- **R4** `apps/attachments/verification.py::verify_attachment(case,
  attachment) -> None` (chamado pela task do slice 002 no fim do pipeline do
  anexo): monta `messages` do prompt com `anonymized_text` + token; chama
  `get_llm_client().complete(settings.LLM1_MODEL, messages, json_schema=...)`
  com schema strict `{patient_match: enum, summary: str, evidence: str}`;
  parse (padrão `apps/pipeline/json_parser.py`); persiste
  `patient_match`/`verification_summary`/`verification_evidence` +
  `status=processed` + `processed_at` + evento `CASE_ATTACHMENT_PROCESSED`
  (payload match/método) no MESMO atomic; falha (LLM/parse) → `failed` +
  `failed_reason` + `CASE_ATTACHMENT_FAILED`, sem efeito no caso.
- **R5** Task integra: `process_case_attachments` passa a chamar
  extração → `anonymize_attachment_text` (persiste `anonymized_text`+
  `pseudonym_map` na row) → `verify_attachment`; anexo sem texto extraído
  (vazio após extração local) → `failed` com motivo claro.
- **R6** Invariante LLM-só-vê-tokens: teste com dados reais (nome do
  paciente + CPF-like no texto do anexo) — o `messages` capturado (fake
  client) não contém NENHUM valor real dos mapas do anexo/caso (assert
  recursivo), contém o token do paciente; resultado `match` persistido
  happy-path; `mismatch` quando o documento é de outro paciente (fake
  retorna mismatch); `unknown` sem token do paciente.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/anonymization/services.py` | `test_attachment_map_converges_with_case`, `test_case_map_untouched` |
| R2 | `apps/attachments/verification.py` | `test_patient_token_lookup`, `test_no_patient_token_yields_unknown` |
| R3 | `apps/llm/prompts_seed.py` | `test_seed_prompts_idempotent_with_verification` |
| R4 | `apps/attachments/verification.py` | `test_verify_match_persists`, `test_verify_mismatch_persists`, `test_verify_llm_failure_failed` |
| R5 | `apps/attachments/tasks.py` | `test_full_attachment_pipeline_match` (extração→anonimização→verificação) |
| R6 | `apps/attachments/tests/test_verification.py` | `test_llm_input_tokens_only` (assert recursivo) |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/anonymization/services.py           # +anonymize_attachment_text (aditivo)
  - apps/attachments/verification.py
  - apps/attachments/tasks.py                # integra o pipeline do anexo
  - apps/llm/prompts_seed.py                 # +ATTACHMENT_VERIFICATION
  - apps/cases/events.py                     # +CASE_ATTACHMENT_PROCESSED
  - apps/attachments/tests/{test_verification.py,test_pipeline_integration.py}
  - apps/anonymization/tests/test_attachment_anonymization.py

out_of_scope:
  - UI (slice 004); closure (slice 005)
  - mudar anonymize_case_text / núcleo engine (wrapper apenas)
  - build_case_prompts (o prompt de verificação NÃO usa o mecanismo por-perfil)
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/attachments/tests/test_verification.py`
- Falha esperada: `ImportError: cannot import name 'verify_attachment'`.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/attachments/tests/
  apps/anonymization/tests/ apps/llm/tests/` — exit 0 (regressão prompts/
  anonimização).
- `uv run ruff check apps/attachments apps/anonymization apps/llm &&
  uv run ruff format --check apps/attachments apps/anonymization apps/llm`
- `uv run mypy .`

## Critérios de aceitação

- [ ] R1–R6 comprovados; LLM de verificação recebe EXCLUSIVAMENTE tokens
      (assert recursivo com dados reais no texto do anexo)
- [ ] Mapa do caso intocado; mapa do anexo persistido na row
- [ ] Resultado/estado/evento no mesmo atomic; falha → failed sem efeito
- [ ] Seed idempotente (29º prompt); gate parcial do slice verde
