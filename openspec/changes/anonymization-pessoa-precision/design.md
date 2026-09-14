# Design: anonymization-pessoa-precision

## Context

- Anonimização (change `presidio-anonymization`): extração determinística
  (paciente/CNS/datas do linkage) + Presidio analyzer (spaCy pt-BR NER
  mapeado `NER_ENTITY_TYPE_TO_CATEGORY`) + merge (`merge_span_candidates`,
  determinístico vence empate) + `PseudonymOperator` (token estável,
  `pseudonym_map` com o valor REAL original).
- Incidente real (3ca56d86): NER marcou termos clínicos como PER → entradas
  PESSOA falsas no mapa ("Afebril", "Diurese", "DORSO", "DOENÇA",
  "VENT.:Ar Ambiente", "PAS:100\nPAD:70", "Elicris") → guard
  `llm1_token_leak` (containment de substring contra TODOS os valores do
  mapa) dispara em qualquer relatório real que discuta esses termos.
- Notas do Eon (endossadas): (1) os 7 falsos positivos morrem pela regra de
  FORMA sozinha — stoplist é defesa em profundidade; (2) all-caps multi-token
  ("DOENÇA ARTERIAL PERIFÉRICA" em doc caps) passa no filtro de forma —
  all-caps NÃO é evidência de nome sem contexto de assinatura; (3) scores no
  report: YAGNI.

## Goals / Non-Goals

- **Goal**: PESSOA do NER só entra no mapa com forma plausível de nome;
  vocabulário clínico (qualquer caixa) nunca vira PESSOA; recall das
  entidades críticas preservado (paciente determinístico; profissionais via
  contexto de assinatura; Title Case multi-token sempre aceito).
- **Non-goal**: mexer no guard/threshold/modelo; OCR de anexos; prompts;
  UI/UX do report.

## Decisions

### D1 — Portão de forma PESSOA (`apps/anonymization/pessoa_shape.py` novo)

Função pura `is_plausible_person(candidate_text: str, *, context: str, offset: int, offset_end: int) -> bool`,
aplicada SOMENTE a `SpanCandidate(category=PESSOA, deterministic=False)` em
`anonymize_text` (antes do merge; determinísticos nunca passam pelo portão):

1. **≥2 tokens** (split em whitespace; 1 token → rejeita).
2. **Estritamente alfabético**: cada token casa `^[A-Za-zÀ-ÿ]+(?:[-'’][A-Za-zÀ-ÿ]+)*$`
   — dígito, dois-pontos, quebra, "%" etc. em QUALQUER lugar → rejeita
   (mata "VENT.:Ar Ambiente" e "PAS:100\nPAD:70").
3. **Evidência de caixa** (nota 2): OU (a) Title Case — ao menos um token
   com inicial maiúscula e resto minúsculo (partículas de ligação
   minúsculas ok: "da", "de", "dos", "e") — OU (b) **contexto de
   assinatura**: all-caps aceito somente se a janela de ±80 chars do texto
   original contiver marcador `CRM|CRF|COREN|COREN-[A-Z]{2}|DRA?\.\s|ENF\.|ASSIN`
   (case-insensitive). All-caps SEM assinatura → rejeita ("DOENÇA ARTERIAL
   PERIFÉRICA" morre aqui).
4. **Stoplist clínica** (defesa em profundidade, nota 1): frase normalizada
   (lowercase, sem acento/ pontuação/quebra) contra lista mínima observada:
   `afebril, diurese, dorso, doenca, vent, pas, pad, elixir/elicris,`
   `doenca arterial periferica` (+ o que o corpus do incidente mostrar) —
   match → rejeita mesmo com forma ok.

### D2 — Aplicação no núcleo, sem tocar nas bordas

Em `services.anonymize_text`, entre a coleta dos `analyzer_results` e o
`merge_span_candidates`: candidatos NER mapeados a PESSOA passam pelo
portão; outras categorias (DATA/LOCAL/CNS/OCORRENCIA/PII) inalteradas.
`anonymize_case_text`/`anonymize_attachment_text` herdam por construção
(ambos chamam o núcleo). Recall protegido: paciente sempre entra pela
extração determinística (seed, sem portão); profissionais aparecem em
assinaturas (regra 3b); Title Case multi-token sempre passa (3a).

### D3 — Corpus de regressão (fragmentos do incidente = vocabulário genérico)

`apps/anonymization/tests/test_pessoa_shape.py` + extensão do corpus
sintético existente (se houver) com:
- Negativos (rejeitados): "Afebril", "Diurese", "DORSO", "DOENÇA",
  "Elicris" (1 token); "VENT.:Ar Ambiente" (dois-pontos);
  "PAS:100\nPAD:70" (dígitos+quebra); "DOENÇA ARTERIAL PERIFÉRICA" em
  contexto all-caps SEM assinatura (nota 2).
- Positivos (tokenizam como PESSOA): "Maria da Silva Santos" (Title Case,
  partícula minúscula); "DR. JOÃO DA SILVA — CRM 12345" em doc all-caps
  (assinatura); "Ana-Lúcia O'Neill" (hífen/apóstrofo).
- Invariante: paciente determinístico entra no mapa mesmo em doc all-caps
  sem assinatura (seed).
- Não-vacuidade: mutação do portão (regra removida) faz os negativos
  falharem.

### D4 — Diagnóstico operacional mínimo

Contador de candidatos PESSOA rejeitados por forma/caça/stoplist no
`anonymization_report` (só contagens, sem textos) — visível no trilho
existente; sem log de valores (padrão do guard d6845cc).

## Risks / Trade-offs

- **Falso-negativo de PESSOA real** (nome rejeitado → fica no texto
  anonimizado): mitigado por D2 (paciente determinístico) + 3b (assinaturas)
  + Title Case sempre-aceito. Residual: terceiro citado em corpo all-caps
  sem marcador — aceito explicitamente (o corpus documenta; a alternativa —
  aceitar todo all-caps — reintroduz a falha sistêmica do incidente).
- Stoplist mínima pode precisar crescer com uso real → operação documentada
  (adicionar termo + teste), não carrego primário do desenho.

## Open Questions

Nenhuma — desfecho do incidente e notas do Eon incorporados; validação final
é a repro dev com o PDF real (antes/depois) acordada com o owner.
