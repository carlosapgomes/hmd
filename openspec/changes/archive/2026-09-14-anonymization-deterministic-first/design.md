# Design: anonymization-deterministic-first

## Context

- `anonymize_text` (services.py:83): `run_deterministic_extraction` →
  `get_anonymization_engine()` → `engine.analyzer.analyze(...)` →
  `_deterministic_candidates` (TODAS as ocorrências dos valores extraídos) +
  candidatos NER → merge (determinístico vence empate) → `PseudonymOperator`.
- Incidente 3ca56d86: mapa envenenado por NER (PESSOA "Afebril"/"Código"/
  "ARTERIOGRAFIA"; LOCAL "FC:77\nFR:18"/"Mot"/"Solicit"…) → guard de
  substring dispara em todo caso real. Mecanismo: valores curtos/comuns
  colidem com texto natural do artefato ("Prof" ⊂ "profissional").
- ats-web (referência em produção): envia `extracted_text` cru ao LLM —
  nenhuma anonimização. A camada determinística do HMD é estritamente MELHOR
  que a referência para o paciente (tokeniza TODAS as ocorrências de
  nome/CNS/CPF/nascimento/ocorrência); o NER cobria apenas terceiros.
- Callers de `anonymize_text`: `anonymize_case_text` (worker-anonymization),
  `anonymize_attachment_text` (idem), `pipeline/prior_case.py:145`
  (`_anonymized_reason`, cluster llm, SEM seed_map hoje).
- Testes: 22 usos de `_stub_engine` (monkeypatch do analyzer) assumem o
  caminho NER; `test_benchmark` roda o comando REAL com corpus contendo
  `_ADVERSARIAL_NAME = "MNQTX ZKWRV"` (recall do NER).
- Guard `_assert_tokens_only` (llm1_service) + `llm2_token_leak`: varrem
  TODOS os valores do `pseudonym_map` como substring — com mapa limpo
  (valores determinísticos longos/alta entropia), colisões ≈ zero.

## Goals / Non-Goals

- **Goal**: fase 2 com mapa determinístico-only (setting default False),
  reversível; guard confiável; prior-case semeado; incidente pinnado.
- **Non-goal**: remover NER/Presidio; mexer no guard; alterar a extração
  determinística; anonimizar terceiros.

## Decisions

### D1 — `ANONYMIZATION_USE_NER` (default False, fail-closed invertido)

`config/settings/base.py`: `ANONYMIZATION_USE_NER = os.environ.get(...) in
("true","1","yes")` (default False — postura nova). Em `anonymize_text`:
quando False, PULA `get_anonymization_engine()`/`analyze` (engine nem é
construído — worker sem spaCy na mem); candidatos = só determinísticos;
merge/operação/report inalterados. `config/settings/test.py` pina **True**
(a suíte segue exercitando o caminho NER com os stubs; imunidade a env
hostil como `UNIT_LABELS`). Novos testes do caminho OFF usam
`override_settings(ANONYMIZATION_USE_NER=False)`.

### D2 — Benchmark segue calibrando o NER

`anonymization_benchmark` lê o setting; a semântica documentada do comando é
"calibrar a camada NER" — o teste de CI do corpus sintético (adversarial)
roda com `override_settings(ANONYMIZATION_USE_NER=True)`. Docstring/`--help`
e README registram: benchmark com NER desligado mede só a camada
determinística (útil p/ baseline).

### D3 — `prior_case` semeado

`_anonymized_reason(prior_case)`: passa `seed_map=prior_case.pseudonym_map`
(tipo anotado já compatível) — nomes do paciente no motivo de negatura
ganham o token do caso mesmo sem rótulo SESAB no texto do motivo. Teste:
motivo citando o nome do paciente → token `<PESSOA_1>` do seed no texto
anonymizado enviado ao LLM2.

### D4 — Pino E2E do incidente (guard com mapa limpo)

Teste novo em `apps/pipeline/tests/` (ou anonymization): texto sintético com
os fragmentos do incidente (rótulos SESAB de paciente + corpo com "Afebril",
"DORSO", "PAS:100\nPAD:80", "FC:77\nFR:18", "Motivo da Solicitação", "Prof",
"Reg", "Macro", "Murmúrio", "DOENÇA ARTERIAL PERIFÉRICA" all-caps) →
`anonymize_text(..., override NER off)` → mapa contém APENAS entradas
determinísticas (nome/CNS/nascimento/ocorrência do paciente) → nenhum
fragmento clínico tokenizado; e `_assert_tokens_only(case, artefato)` PASSA
com artefato representativo contendo "profissional", "Registro",
"Macro" (natural do vocabulário do LLM). Espelho do bug: sem este change, o
mesmo teste falha (RED).

### D5 — Compose passthrough + docs

Default False já é o posture (nenhuma env obrigatória nova). A alavanca de
reativação PRECISA de passthrough explícito no compose (allowlist por
serviço): `ANONYMIZATION_USE_NER: ${ANONYMIZATION_USE_NER:-false}` em
`worker-anonymization` E `worker-attachments` (este também anonimiza —
`apps/attachments/tasks.py:221`), nos DOIS composes (prod e dev). Sem isso o
`.env` do host não alcança o container. `.env.example` documenta
`#ANONYMIZATION_USE_NER=true` (reativação + benchmark p/ calibrar). README:
decisão da política (terceiros = postura ats-web; paciente tokenizado),
mem do worker cai com NER off, rollback de política = 1 env + `up -d`.

### D6 — Report truthful (P2-3 da review)

`_anonymization_report` passa a incluir `"ner_enabled":
settings.ANONYMIZATION_USE_NER` — o relatório de auditoria nunca descreve
engine que não rodou (com NER off, `model`/versões Presidio são omitidos ou
marcados; implementação escolhe a forma mais simples truthful).

### Correções de seam (P2-4)

- Prior-case: o ponto real é `_anonymized_reason(reason: str)`
  (`prior_case.py:141`) alimentado por `_build_summary` com `row.case`
  (`select_related`, `:83`); o seed é `row.case.pseudonym_map`.
- Não há factory de Case em `apps/pipeline/tests/` (conftest do
  anonymization só tem fixtures CPF/CNS): Cases são construídos inline
  (`Case.objects.create(created_by=…)`).
- E2E deve usar rótulos SESAB canônicos (o extrator de nome só para no
  próximo rótulo canônico) e manter fora do artefato os valores seed
  (dígitos de CPF/CNS, datas).

## Risks / Trade-offs

- **Terceiros em claro ao OpenRouter** (decisão do dono, 2026-09-14): médico
  solicitante/acompanhante citados no texto seguem no prompt. Mitigações:
  política documentada; reativação futura por env; guard continua ativo
  (paciente). Igual ou melhor que a referência em produção.
- **Motivos de negatura** sem rótulo: fechado por D3 (seed).
- Suíte: caminho NER segue testado via pin True (regressão do opt-in
  coberta pelos novos testes OFF).

## Open Questions

Nenhuma — decisão do dono registrada; Eon endossou o determinístico-first
(com a ressalva do guard intocado, atendida).
