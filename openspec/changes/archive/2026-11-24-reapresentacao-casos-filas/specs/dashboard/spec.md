# Spec Delta

## MODIFIED Requirements

### Requirement: Métricas por período sem dados de paciente

O sistema SHALL exibir painel por período (hoje, 7 dias, 30 dias, tudo) com fontes imutáveis de resposta final/origem, decisões e unidade, preservando resultados de casos limpos e contagem de encerramentos administrativos. Métricas SHALL conter só contagens, tempos e labels, sem identificação de paciente; listagem mantém identificação completa. Encerramentos administrativos e por reapresentação SHALL sair de Em andamento. Total do resumo conta envios recebidos e Encerrados conta CLEANED, inclusive técnicos; reapresentação não cria desfecho clínico ou administrativo.

#### Scenario: Resumo do período
- **GIVEN** casos agendados, negados e em andamento no período
- **WHEN** painel é aberto
- **THEN** total, agendados, negados e em andamento refletem os casos sem desfecho fictício

#### Scenario: Casos limpos continuam contados
- **GIVEN** caso limpo pela ciência dentro do período
- **WHEN** painel é aberto
- **THEN** resultado real continua contado como agendado ou negado

#### Scenario: Página sem dados de paciente
- **GIVEN** casos com nomes e registros
- **WHEN** painel é renderizado
- **THEN** seção de métricas não contém identificação; listagem continua exibindo identificação completa

#### Scenario: Período selecionável
- **WHEN** período de 7 dias é selecionado
- **THEN** métricas usam a população desse período conforme os marcos existentes

#### Scenario: Encerramentos administrativos contados no período
- **WHEN** existem encerramentos administrativos no período
- **THEN** contam na métrica administrativa e de encerrados, sem entrar em Em andamento

#### Scenario: Encerramento técnico não permanece em andamento
- **GIVEN** original substituído ou incoming arquivado por reapresentação, sem decisão clínica
- **WHEN** resumo é calculado, inclusive com feature desligada
- **THEN** esse envio conta em Total e Encerrados, mas não em Em andamento, agendados, negados ou encerramentos administrativos

### Requirement: Quebra por tipo de procedimento e unidade

O sistema SHALL quebrar métricas por tipo (total, aprovados, negados, sem decisão, na ordem canônica) e unidade (1 e 2), com tempo médio até decisão médica. Casos encerrados por reapresentação SHALL ser excluídos de todas as contagens por procedimento, inclusive Total e Pendentes; auditoria e listagem preservam esses envios. Resultados reais do representante mantido permanecem contados.

#### Scenario: Tabela por tipo com decisões
- **GIVEN** pedidos não encerrados por reapresentação com procedimentos declarados/decididos
- **WHEN** painel é aberto
- **THEN** tabela mostra total, aprovados, negados e sem decisão por tipo

#### Scenario: Agendados por unidade
- **WHEN** há casos realmente agendados em ambas as unidades
- **THEN** quebra por unidade exibe as contagens reais de cada unidade

#### Scenario: Tempo médio até a decisão
- **WHEN** há decisões médicas registradas no período
- **THEN** tempo médio entre início do caso e decisão médica aparece legível, sem decisão inventada para encerramentos técnicos

#### Scenario: Reenvios não inflam demanda por procedimento
- **GIVEN** um pedido mantido e três envios encerrados por reapresentação
- **WHEN** tabela por procedimento é calculada
- **THEN** somente o mantido contribui às contagens; os três encerrados continuam encontráveis na listagem e auditoria
