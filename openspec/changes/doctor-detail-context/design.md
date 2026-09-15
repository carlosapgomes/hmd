# Design: doctor-detail-context

## Contexto

O detalhe médico (`templates/doctor/case_detail.html`) renderiza cards na
ordem em que o pipeline os produziu historicamente: Identificação (com
Paciente/Nº ocorrência/Nascimento) → Procedimentos declarados (~L55) →
Decisões registradas (~L78, condicional à decisão) → Alertas consultivos
(~L118) → Recomendação agregada (~L192) → Requisitos gerais (~L214) →
Prior-case (~L232) → Sumário clínico (~L268) → Estrutura extraída (~L284) →
Trilha (~L308) → Documentos (~L341) → Anexos (~L380). O presenter
`build_case_detail_context` (`apps/doctor/presenters.py` ~656) monta
`identification` a partir do `Case`.

## D1 — Demografia no card de identificação (campos, não cálculo)

`identification` ganha `patient_age`/`patient_gender`/`patient_race`
direto dos campos do caso (extração determinística do cabeçalho SESAB;
`None`/vazio quando ausentes). Sem derivação ou formatação esperta — a
idade é a do cabeçalho (momento do relatório), o sexo é `F`/`M`
normalizado pela extração, a raça é a forma do enum IBGE. O template
renderiza «Idade» (com «a» de anos: «84 a»), «Sexo» (F/M) e «Raça/Cor» com
`—` quando ausentes — mesmo contrato dos campos existentes do card.

## D2 — Ordem de leitura clínica (movimento mínimo)

Alvo: Identificação → Procedimentos declarados → **Sumário clínico** →
**Estrutura extraída** → Decisões registradas → Alertas consultivos →
Recomendação agregada → Requisitos gerais → Prior-case → Trilha →
Documentos → Anexos. Implementação: mover os DOIS blocos (sumário e
estrutura) para logo após o bloco de procedimentos declarados; posições
relativas de todos os demais blocos preservadas (decisões registradas fica
após a estrutura, entre o quadro clínico e o consultivo — em `AWAITING_DOCTOR`
o card não renderiza e a ordem é exatamente a pedida). O mesmo template
serve ao detalhe decidido read-only — ordem única, sem variante.

## D3 — Não-mudanças

- Presenter de re-identificação (tokens→valores) intocado; demografia é
  campo estrutural (nunca passou por LLM/token).
- Filas (item 1 do backlog) e trilha (item 4) são changes próprios.
- Especificação de closure/minimização intocada (campos já cobertos pelo
  change do cabeçalho: preservados no CLEANED).
