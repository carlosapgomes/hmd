# anonymization Specification (delta)

## ADDED Requirements

### Requirement: Precisão de PESSOA em registro clínico

Candidatos de PESSOA originados exclusivamente do NER (não os
determinísticos) SHALL passar por um portão de forma determinístico antes de
receberem token: o candidato SHALL ter ao menos dois tokens estritamente
alfabéticos (sem dígitos, dois-pontos ou quebras) e evidência de nome —
Title Case OU contexto de assinatura (marcador profissional como CRM/Coren
ou tratamento como "Dr(a)." na vizinhança). Texto todo-maiúsculo SEM
contexto de assinatura NÃO constitui evidência de nome. Uma stoplist clínica
minimalista (termos observados em produção) SHALL rejeitar candidatos por
frase normalizada mesmo quando a forma passa. A extração determinística do
paciente e as demais categorias SHALL permanecer inalteradas, e o guard de
tokens do pipeline SHALL continuar exigindo que nenhum valor real do mapa
apareça nos artefatos de LLM.

#### Scenario: Termo clínico de token único não vira PESSOA

- **GIVEN** um relatório contendo "Afebril", "Diurese", "DORSO" ou "DOENÇA"
  marcados como PER pelo NER
- **WHEN** a anonimização do caso é executada
- **THEN** nenhum desses termos entra no pseudonym_map nem é substituído por
  token de PESSOA

#### Scenario: Candidato com dígitos ou pontuação estrutural é rejeitado

- **GIVEN** candidatos como "VENT.:Ar Ambiente" (dois-pontos) e
  "PAS:100\nPAD:70" (dígitos e quebra)
- **WHEN** a anonimização é executada
- **THEN** eles não entram no pseudonym_map

#### Scenario: Termo clínico todo-maiúsculo sem assinatura não vira PESSOA

- **GIVEN** um relatório em caixa alta contendo "DOENÇA ARTERIAL
  PERIFÉRICA" sem marcador de assinatura na vizinhança
- **WHEN** a anonimização é executada
- **THEN** a expressão não entra no pseudonym_map como PESSOA

#### Scenario: Nome em Title Case é tokenizado

- **GIVEN** um relatório contendo um nome próprio multi-token em Title Case
  (ex.: "Maria da Silva Santos", com partícula de ligação minúscula)
- **WHEN** a anonimização é executada
- **THEN** o nome recebe token de PESSOA e entra no pseudonym_map

#### Scenario: Nome todo-maiúsculo em contexto de assinatura é tokenizado

- **GIVEN** um relatório em caixa alta contendo "DR. JOÃO DA SILVA —
  CRM 12345"
- **WHEN** a anonimização é executada
- **THEN** o nome recebe token de PESSOA (o marcador de assinatura é a
  evidência de nome)

#### Scenario: Paciente determinístico independe do portão

- **GIVEN** um caso cujo paciente tem nome em caixa alta no relatório sem
  contexto de assinatura
- **WHEN** a anonimização é executada
- **THEN** o nome do paciente é anonimizado pela extração determinística
  (seed), sem passar pelo portão de forma

#### Scenario: Stoplist clínica rejeita por frase normalizada

- **GIVEN** um candidato multi-token Title Case cuja frase normalizada está
  na stoplist clínica (termo observado em produção)
- **WHEN** a anonimização é executada
- **THEN** o candidato não entra no pseudonym_map mesmo com forma válida
