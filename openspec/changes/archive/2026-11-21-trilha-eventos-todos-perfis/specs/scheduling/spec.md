# Spec Delta

## Purpose

N/A — capability existente (`scheduling`); delta modifica requisito vigente.

## MODIFIED Requirements

### Requirement: Visão do agendador limitada ao necessário

O detalhe do caso para o agendador SHALL exibir apenas a identificação do paciente (nome, data de nascimento, número de ocorrência), o diagnóstico resumido (primeira linha do sumário, re-identificada apenas na renderização), os procedimentos com as decisões médicas (incluindo motivos das negativas), os dados de agendamento, a thread de comunicações e, ao final, a trilha de eventos em card collapsible «Trilha de eventos» (a mesma do painel: rótulos legíveis com badge por categoria, ator com papel em português, data/hora; fechado por padrão) — sem os demais artefatos do pipeline (linhas seguintes do sumário, estrutura extraída, alertas da policy, sugestão). O `<title>` e o breadcrumb do detalhe SHALL usar a **referência humana** do caso — nº de ocorrência com fallback `uid <8 primeiros chars>` (padrão do painel) — e o uid completo NÃO aparece em texto visível (permanece apenas como chave das URLs). O PDF do relatório original e o texto extraído SHALL ficar disponíveis ao agendador somente após a decisão de agendamento, para casos processados por ele mesmo — por documento do relatório (o relatório pode chegar em múltiplos PDFs ordenados) — nos cards `Texto Extraído do PDF` e `Visualizar PDF Original` (spec `pdf-original-viewer`).

#### Scenario: Agendador vê o diagnóstico resumido re-identificado

- **GIVEN** um caso com `summary_text` de múltiplas linhas contendo pseudônimos e o mapa de pseudônimos do caso
- **WHEN** o agendador abre o detalhe do agendamento
- **THEN** apenas a primeira linha do sumário é exibida, já re-identificada (sem tokens), e nenhuma outra linha do sumário aparece na página

#### Scenario: Agendador não vê artefatos clínicos além do permitido

- **GIVEN** um caso com sumário completo, estrutura extraída e alertas da policy persistidos
- **WHEN** o agendador abre o detalhe do agendamento
- **THEN** identificação, diagnóstico resumido e decisões médicas são exibidos e nenhum conteúdo de estrutura extraída, alerta da policy ou sugestão aparece na página

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

#### Scenario: Texto extraído sob o mesmo gate do PDF

- **GIVEN** um caso confirmado pelo agendador autenticado, com texto extraído
- **WHEN** ele abre o detalhe do agendamento
- **THEN** o card `Texto Extraído do PDF` exibe o texto; em caso de outro agendador ou fora do gate, nem o card de texto nem o de PDF aparecem

#### Scenario: Trilha do agendador mostra quem decidiu e quem criou

- **GIVEN** um caso pronto para agendamento, criado por um NIR, com decisão médica registrada por um médico
- **WHEN** o agendador abre o detalhe e expande a trilha de eventos
- **THEN** os eventos de criação e de decisão médica exibem o nome do ator com o papel em português e a data/hora; o card permanece fechado por padrão e nenhuma linha adicional do sumário aparece
