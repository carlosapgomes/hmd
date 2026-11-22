# Spec Delta

## ADDED Requirements

### Requirement: Badge de transporte nos cards da fila do agendador

Os cards da fila do agendador (abas Pendentes e Processados Hoje) SHALL exibir um badge com o tipo de transporte do caso quando presente: `UTI Móvel` com ênfase visual de gravidade e `Ambulância Básica` em estilo neutro, com rótulos canônicos — mesma apresentação das demais filas (paridade de sinal entre papéis). Caso sem transporte extraído não exibe badge.

#### Scenario: Card com UTI Móvel exibe badge de ênfase

- **GIVEN** um caso em `AWAITING_SCHEDULING` com tipo de transporte `mobile_icu`
- **WHEN** o agendador acessa a aba Pendentes
- **THEN** o card exibe o badge «UTI Móvel» em estilo de ênfase de gravidade, junto da demografia disponível

#### Scenario: Card com Ambulância Básica exibe badge neutro

- **GIVEN** um caso na fila do agendador com tipo de transporte `basic`
- **WHEN** o agendador acessa a fila
- **THEN** o card exibe o badge «Ambulância Básica» em estilo neutro

#### Scenario: Card sem transporte não exibe badge

- **GIVEN** um caso na fila do agendador sem transporte extraído
- **WHEN** o agendador acessa a fila
- **THEN** o card não exibe badge de transporte, sem placeholder
