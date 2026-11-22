# Spec Delta

## ADDED Requirements

### Requirement: Extração do tipo de transporte do relatório

O processamento no cluster pdf SHALL extrair o tipo de transporte do label estruturado `TRANSPORTE:` (linha iniciada pelo rótulo com valor na mesma linha) sobre o texto do caso — `extracted_text`, concatenado na ordem declarada dos documentos —, persistindo-o no campo do caso com domínio fechado: `Ambulância Básica` (inclusive a forma abreviada `BÁSICA`) → `basic`; `UTI Móvel` → `mobile_icu`; casamento tolerante a acento/caixa. Com múltiplas ocorrências no texto do caso, a última vence. Label ausente, valor vazio ou valor fora do domínio SHALL deixar o campo vazio — nunca inventado; menção narrativa sem o formato de rótulo não é capturada. O valor não vai a eventos e, como metadado administrativo não-identificante, SHALL sobreviver à minimização do `CLEANED` em ambos os modos, no molde dos metadados do cabeçalho.

#### Scenario: Label básico popula o caso

- **GIVEN** um relatório com a linha `TRANSPORTE: Ambulância Básica` no texto do relatório
- **WHEN** a task de processamento executa
- **THEN** o caso fica com o tipo de transporte `basic` e o ciclo segue normalmente

#### Scenario: UTI Móvel popula o caso

- **GIVEN** um relatório com a linha `TRANSPORTE: UTI Móvel`
- **WHEN** a task de processamento executa
- **THEN** o caso fica com o tipo de transporte `mobile_icu`

#### Scenario: Forma abreviada é normalizada

- **GIVEN** um relatório com a linha `TRANSPORTE: BÁSICA`
- **WHEN** a task de processamento executa
- **THEN** o caso fica com o tipo de transporte `basic` (mesma categoria da forma completa)

#### Scenario: Valor fora do domínio não inventa badge

- **GIVEN** um relatório com a linha `TRANSPORTE: Aéreo`
- **WHEN** a task de processamento executa
- **THEN** o campo de transporte fica vazio e o caso é processado sem erro

#### Scenario: Label ausente deixa campo vazio

- **GIVEN** um relatório sem o label `TRANSPORTE:`
- **WHEN** a task de processamento executa
- **THEN** o campo de transporte fica vazio e o caso é processado normalmente

#### Scenario: Múltiplas ocorrências elegem a última

- **GIVEN** um relatório com três evoluções contendo `TRANSPORTE: Ambulância Básica` e uma evolução posterior contendo `TRANSPORTE: UTI Móvel`
- **WHEN** a task de processamento executa
- **THEN** o caso fica com o tipo de transporte da última ocorrência (`mobile_icu`)

#### Scenario: Citação de relatório anterior não sobrepõe a evolução atual

- **GIVEN** um relatório cujo complemento cita solicitação anterior com a linha `TRANSPORTE: UTI Móvel` antes da evolução atual com `TRANSPORTE: Ambulância Básica`
- **WHEN** a task de processamento executa
- **THEN** o caso fica com o tipo de transporte da evolução atual (`basic`) — a última ocorrência do texto vence, e citações anteriores não a sobrepõem

#### Scenario: Menção narrativa não é capturada

- **GIVEN** um relatório cuja seção de complemento cita «Transporte em ambulância avançada (…)» sem o formato de rótulo com dois-pontos
- **WHEN** a task de processamento executa
- **THEN** a menção narrativa não altera o campo de transporte (apenas o label estruturado `TRANSPORTE:` conta)

#### Scenario: Reenvio de documento re-extraí o transporte

- **GIVEN** um caso retido pelo gate com `TRANSPORTE: UTI Móvel` cujo documento é reenviado por um sem o label
- **WHEN** o reprocessamento executa
- **THEN** o campo de transporte fica vazio (o worker reatribui o valor a cada extração, sem estado stale)

#### Scenario: Valor de transporte não vai a eventos

- **GIVEN** um relatório processado com transporte extraído
- **WHEN** o evento de extração concluída é gravado
- **THEN** o payload do evento não carrega o valor do transporte

#### Scenario: Minimização do CLEANED preserva o transporte

- **GIVEN** um caso com `transport_type` populado encerrado pela ciência do NIR com minimização ativa
- **WHEN** o caso chega a `CLEANED`
- **THEN** o campo de transporte permanece populado (metadado administrativo não-identificante, fora dos campos zerados pela limpeza)

### Requirement: Badge de transporte nos cards de meus casos

Os cards de «meus casos» do NIR SHALL exibir um badge com o tipo de transporte do caso quando presente: `UTI Móvel` com ênfase visual de gravidade e `Ambulância Básica` em estilo neutro, com rótulos canônicos. Caso sem transporte extraído não exibe badge (ausência não é erro nem placeholder).

#### Scenario: Card com UTI Móvel exibe badge de ênfase

- **GIVEN** um caso ativo do NIR com tipo de transporte `mobile_icu`
- **WHEN** o NIR acessa «meus casos»
- **THEN** o card exibe o badge «UTI Móvel» em estilo de ênfase de gravidade, distinto do badge neutro

#### Scenario: Card com Ambulância Básica exibe badge neutro

- **GIVEN** um caso ativo do NIR com tipo de transporte `basic`
- **WHEN** o NIR acessa «meus casos»
- **THEN** o card exibe o badge «Ambulância Básica» em estilo neutro

#### Scenario: Card sem transporte não exibe badge

- **GIVEN** um caso ativo sem transporte extraído
- **WHEN** o NIR acessa «meus casos»
- **THEN** o card não exibe badge de transporte e a linha de metadados permanece íntegra
