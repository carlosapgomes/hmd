# Proposal: anonymization-pessoa-precision

## Why

Caso real da fase 2 (3ca56d86, relatório de arteriografia) falhou em
`llm1_token_leak` de forma **sistêmica**: o NER do spaCy (pt_core_news_lg)
classifica vocabulário clínico como PESSOA — mapa do caso continha entradas
como "Afebril", "Diurese", "DORSO", "DOENÇA", "VENT.:Ar Ambiente",
"PAS:100\nPAD:70" como `<PESSOA_n>`. Como esse vocabulário é nuclear em
QUALQUER relatório, o artefato do LLM1 sempre contém essas substrings → o
guard `llm1_token_leak` dispara em **todo caso real** (fail-closed
garantido). Nenhum PHI vazou (falsos positivos não são pessoas); o guard e
as invariantes de privacidade estão corretos — o mapa é que está
envenenado. Reprodução e validação: dev local com o PDF real (ambiente já
ativo, commit d6845cc).

## What Changes

- **Filtro determinístico pós-NER específico de PESSOA** (nota 1 do Eon: os
  7 falsos positivos conhecidos morrem pela REGRA DE FORMA sozinha):
  candidato PESSOA oriundo do NER só vira token se tiver forma plausível de
  nome — ≥2 tokens; tokens estritamente alfabéticos (letras/hífen/apóstrofo,
  sem dígitos/dois-pontos/quebras); e evidência de caixa: Title Case (inicial
  maiúscula + resto minúsculo) OU contexto de assinatura (nota 2 do Eon:
  all-caps NÃO é evidência de nome por si — "DOENÇA ARTERIAL PERIFÉRICA" num
  relatório todo-maiúsculo passaria no filtro de forma; all-caps só entra
  adjacente a marcador de assinatura: CRM/CRF/Coren, "Dr(a).", "Enf.",
  "assin…").
- **Stoplist clínica como defesa em profundidade** (não carrego primário —
  nota 1: cresce mal): match por frase normalizada (lowercase, sem
  acento/quebra) contra lista mínima de termos já vistos em produção
  ("afebril", "diurese", "dorso", "doença", …); rejeita mesmo se a forma
  passar.
- **Corpus de regressão** com os fragmentos reais do incidente (vocabulário
  clínico genérico — sem dado de paciente) + controles positivos (nome real
  Title Case multi-token; nome all-caps EM contexto de assinatura) e o caso
  all-caps clínico multi-token ("DOENÇA ARTERIAL PERIFÉRICA" em doc caps).
- Inalterados: extração determinística (paciente/CNS/datas — seed),
  `ANONYMIZATION_SCORE_THRESHOLD` (recall de PHI real não pode ser
  sacrificado), guard `_assert_tokens_only`, threshold do Presidio.

## Capabilities

### Modified: `anonymization`

- ADDED requirement "Precisão de PESSOA em registro clínico" com os cenários
  de forma/caixa/assinatura/stoplist e os controles positivos.

## Impact

- Sem migration; sem mudança de settings; sem UI. Muda o núcleo puro
  (`apps/anonymization/services.py`/novo módulo do filtro) + testes.
- Risco gerenciado no desenho: rejeitar PESSOA real = PHI fica no texto
  anonimizado → mitigado por (i) paciente sempre determinístico, (ii)
  contexto de assinatura cobre nomes de profissionais, (iii) corpus com
  controles positivos.
- Pós-change: release v0.1.9 (ciclo normal), update no Eon, e só então
  reenvio do relatório (novo caso).

## Não-goais

- Não mexe no guard, no threshold global, no modelo spaCy (troca/md fica
  como fallback operacional já existente via `ANONYMIZATION_SPACY_MODEL`);
  não coleta scores no report (YAGNI — nota 3 do Eon); não amplia a stoplist
  além do observado.
