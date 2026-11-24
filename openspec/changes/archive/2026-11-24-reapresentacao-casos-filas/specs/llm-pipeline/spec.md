# Spec Delta

## MODIFIED Requirements

### Requirement: Prior-case com fallback por nome e nascimento

O sistema SHALL localizar contexto de decisões anteriores por procedimento usando a identidade de `case-reappearance`: nome normalizado e regulação ou CNS válido iguais, ou confirmação NIR vigente de candidato fraco/conflitante. O lookup SHALL excluir o próprio caso e decisões futuras, usar janela única configurável de 15 dias após a decisão e produzir data, desfecho, motivo anonimizado e contagem de negativas. Origens SHALL ser `occurrence_number`, `patient_cns`, `nir_confirmed` ou `none` no evento sem match. Nascimento deixa de ser necessário no modo novo. Feature desligada SHALL preservar lookup legado de regulação 7 dias e nome+nascimento 15 dias, com suas settings depreciadas; feature ligada usa só a janela nova.

#### Scenario: Match por número de ocorrência dentro da janela
- **GIVEN** caso prévio decidido há 3 dias com nome e número de ocorrência iguais
- **WHEN** lookup executa para o mesmo procedimento
- **THEN** resumo do prévio é retornado com origem `occurrence_number` e evento auditável

#### Scenario: Fallback por nome+nascimento dentro de 15 dias
- **GIVEN** caso prévio decidido há 10 dias com regulação diferente, nome e CNS válidos iguais, sem nascimento
- **WHEN** lookup executa
- **THEN** resumo é retornado com origem `patient_cns` e evento correspondente, sem depender de nascimento

#### Scenario: Sem prévio fora das janelas
- **GIVEN** decisões médicas registradas há mais de 15 dias em relação à criação do novo
- **WHEN** lookup executa
- **THEN** nenhum prévio fora da janela é retornado, mesmo por regulação

#### Scenario: Limites fechados e decisão futura
- **WHEN** decisões ocorreram exatamente na criação, exatamente 15 dias antes ou depois da criação do novo
- **THEN** os dois primeiros limites são elegíveis e a decisão futura é excluída

#### Scenario: Suspeita fraca não vira contexto factual
- **WHEN** só nome+origem coincide e o NIR ainda não respondeu ou marcou independente
- **THEN** caso suspeito não alimenta o contexto do LLM2 nem o card como prévio confirmado

#### Scenario: Negativa encerrada permanece consultiva
- **WHEN** caso CLEANED tem negativa dentro da janela e identidade válida para o novo pedido
- **THEN** decisão anterior e motivo informam o novo ciclo sem reaplicar negativa automaticamente, com motivo anonimizado antes de envio externo

#### Scenario: Confirmação usa código canônico
- **WHEN** candidato fraco ou conflitante é confirmado pelo NIR em revisão vigente
- **THEN** contexto tem origem `nir_confirmed`, com confirmação correlacionada e sem identificadores no evento

#### Scenario: Rollout desligado preserva lookup legado
- **WHEN** feature está desligada e há decisão por regulação há 10 dias sem nascimento
- **THEN** janela legada de 7 dias não retorna match; habilitando feature, mesmo nome e regulação podem casar na janela única de 15 dias

#### Scenario: Contexto anterior coexiste com fila ativa
- **WHEN** existe negativa histórica elegível e candidato ativo válido para o novo
- **THEN** negativa pode informar o contexto e resolução de fila ocorre independentemente, sem consumir exclusividade de vínculo operacional

#### Scenario: Confirmação posterior à sumarização
- **WHEN** NIR confirma vínculo com negativa depois da sumarização do novo
- **THEN** card médico mostra o contexto confirmado e distingue sua inclusão posterior, sem afirmar que o LLM2 já o considerou nem alterar decisão médica registrada
