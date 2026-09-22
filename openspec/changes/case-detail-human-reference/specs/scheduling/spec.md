# Delta: scheduling

## MODIFIED Requirements

### Requirement: Visão do agendador limitada ao necessário

O detalhe do caso para o agendador SHALL exibir apenas a identificação do paciente (nome, data de nascimento, número de ocorrência), o diagnóstico resumido (primeira linha do sumário, re-identificada apenas na renderização), os procedimentos com as decisões médicas (incluindo motivos das negativas), os dados de agendamento e a thread de comunicações — sem os demais artefatos do pipeline (linhas seguintes do sumário, estrutura extraída, alertas da policy, sugestão). O `<title>` e o breadcrumb do detalhe SHALL usar a **referência humana** do caso — nº de ocorrência com fallback `uid <8 primeiros chars>` (padrão do painel) — e o uid completo NÃO aparece em texto visível (permanece apenas como chave das URLs). O PDF do relatório original SHALL ficar disponível ao agendador somente após a decisão de agendamento, para casos processados por ele mesmo — por documento do relatório (o relatório pode chegar em múltiplos PDFs ordenados).

#### Scenario: Agendador vê o diagnóstico resumido re-identificado

- **GIVEN** um caso com `summary_text` de múltiplas linhas contendo pseudônimos e o mapa de pseudônimos do caso
- **WHEN** o agendador abre o detalhe do agendamento
- **THEN** apenas a primeira linha do sumário é exibida, já re-identificada (sem tokens), e nenhuma outra linha do sumário aparece na página

#### Scenario: Agendador não vê artefatos clínicos além do permitido

- **GIVEN** um caso com sumário completo, estrutura extraída e alertas da policy persistidos
- **WHEN** o agendador abre o detalhe do agendamento
- **THEN** identificação, diagnóstico resumido e decisões médicas são exibidas e nenhum conteúdo de estrutura extraída, alerta da policy ou sugestão aparece na página

#### Scenario: PDF disponível apenas de caso processado pelo próprio agendador

- **GIVEN** um caso confirmado pelo agendador autenticado, com dois documentos de relatório
- **WHEN** ele solicita o PDF de cada documento do caso
- **THEN** cada documento original é servido como PDF

#### Scenario: PDF negado a outro agendador ou a caso reaberto

- **GIVEN** um caso confirmado por outro agendador e um caso reaberto por intercorrência (sem agendador vinculado)
- **WHEN** o agendador autenticado solicita o PDF de qualquer um deles
- **THEN** recebe HTTP 404 e nenhum conteúdo do documento é exposto

#### Scenario: Detalhe do agendador usa a referência humana

- **GIVEN** um caso pronto para agendamento com nº de ocorrência
- **WHEN** o agendador abre o detalhe
- **THEN** `<title>` e breadcrumb exibem «Caso <nº>» (`uid <8 chars>` quando o nº está ausente) e o uid completo não aparece em texto visível — apenas nos hrefs
