# Spec Delta

## ADDED Requirements

### Requirement: Badge de transporte nos cards da fila médica

Os cards da fila médica (abas Pendentes e Decididos Hoje) SHALL exibir um badge com o tipo de transporte do caso quando presente: `UTI Móvel` com ênfase visual de gravidade e `Ambulância Básica` em estilo neutro, com rótulos canônicos — mesma apresentação das demais filas (paridade de sinal entre papéis). Caso sem transporte extraído não exibe badge.

#### Scenario: Card pendente com UTI Móvel exibe badge de ênfase

- **GIVEN** um caso em `AWAITING_DOCTOR` com tipo de transporte `mobile_icu`
- **WHEN** o médico acessa a aba Pendentes
- **THEN** o card exibe o badge «UTI Móvel» em estilo de ênfase de gravidade, junto da demografia disponível

#### Scenario: Card com Ambulância Básica exibe badge neutro

- **GIVEN** um caso na fila médica com tipo de transporte `basic`
- **WHEN** o médico acessa a fila
- **THEN** o card exibe o badge «Ambulância Básica» em estilo neutro

#### Scenario: Card sem transporte não exibe badge

- **GIVEN** um caso na fila médica sem transporte extraído
- **WHEN** o médico acessa a fila
- **THEN** o card não exibe badge de transporte, sem placeholder

### Requirement: Transporte no detalhe do caso médico

O detalhe do caso SHALL exibir o tipo de transporte no card de identificação, junto da demografia do paciente, com rótulo legível e valor canônico (`UTI Móvel`/`Ambulância Básica`) — dado determinístico extraído do relatório, re-identificado apenas na renderização como os demais campos do presenter. Transporte ausente exibe o placeholder padrão dos campos vazios do card («—»), sem erro.

#### Scenario: Detalhe exibe transporte junto da demografia

- **GIVEN** um caso em `AWAITING_DOCTOR` com tipo de transporte `mobile_icu`
- **WHEN** o médico autorizado abre o detalhe
- **THEN** o card de identificação exibe a linha de transporte com o valor «UTI Móvel» ao lado de idade/sexo/raça

#### Scenario: Transporte ausente mostra o placeholder padrão

- **GIVEN** um caso sem transporte extraído
- **WHEN** o médico autorizado abre o detalhe
- **THEN** a linha de transporte exibe «—» como os demais campos ausentes do card, sem erro
