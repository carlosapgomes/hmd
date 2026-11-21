# Spec Delta

## Purpose

N/A — capability existente (`doctor-decision`); delta modifica requisitos vigentes.

## MODIFIED Requirements

### Requirement: Presenter re-identificado sob papel autorizado

O detalhe do caso SHALL apresentar, apenas para `doctor`/`admin`, os dados reais do paciente (identificação, número de ocorrência) e a demografia do caso — idade, sexo e raça/cor extraídas do cabeçalho padrão SESAB — e os artefatos do pipeline re-identificados: histórico e sumário, estrutura extraída, alertas da policy com a recomendação por procedimento e o resultado agregado, requisitos gerais acionáveis e o PDF original. A re-identificação SHALL ocorrer apenas na renderização para papel autorizado; nenhum artefato re-identificado é persistido nem enviado a qualquer LLM. A ordem dos cards SHALL seguir a leitura clínica: identificação, «Procedimentos do caso», sumário clínico e estrutura extraída (o quadro clínico) ANTES dos alertas consultivos e demais cards da automação. O detalhe exibe a trilha de eventos em card collapsible «Trilha de eventos» ao final da página — a mesma trilha do painel (paridade total de auditoria): cada evento com rótulo legível, badge por categoria, ator com papel em português e data/hora, card fechado por padrão. O card «Procedimentos do caso» exibe TODAS as rows — inclusive a detectada não-declarada que sobrevive ao bypass da divergência — com badges de origem (Declarado/Detectado na extração) e de detecção após a reconciliação. O `<title>`, o breadcrumb e os links de navegação entre detalhe e decisão SHALL usar a **referência humana** do caso — nº de ocorrência com fallback `uid <8 primeiros chars>` (padrão do painel) — e o uid completo NÃO aparece em texto visível nessas superfícies (permanece apenas como chave das URLs).

#### Scenario: Médico vê o sumário re-identificado

- **GIVEN** um caso em `AWAITING_DOCTOR` cujo `summary_text` contém o token `<PESSOA_1>`
- **WHEN** o médico autorizado abre o detalhe
- **THEN** o sumário é renderizado com o nome real do paciente no lugar do token

#### Scenario: Requisitos gerais acionáveis exibidos conforme alertas

- **GIVEN** um caso cujo policy gerou alerta de anticoagulante com protocolo de suspensão por fármaco
- **WHEN** o médico abre o detalhe
- **THEN** o card do procedimento exibe o requisito geral com o protocolo acionável correspondente ao alerta

#### Scenario: Recomendação consultiva visível por procedimento

- **GIVEN** um caso com `policy_result` recomendando recusar um procedimento com motivos e a sugestão agregada de recusa
- **WHEN** o médico abre o detalhe
- **THEN** os motivos da recusa por procedimento e o agregado são exibidos como alerta consultivo, sem bloquear a decisão

#### Scenario: Demografia do paciente no card de identificação

- **GIVEN** um caso com `patient_age`=84, `patient_gender`=`F` e `patient_race`=`Parda` extraídos do cabeçalho SESAB
- **WHEN** o médico autorizado abre o detalhe
- **THEN** o card de identificação exibe idade (84 a), sexo (F) e raça/cor (Parda) ao lado de nome e nº de ocorrência

#### Scenario: Demografia ausente mostra o placeholder padrão

- **GIVEN** um caso sem cabeçalho SESAB (campos de demografia vazios)
- **WHEN** o médico autorizado abre o detalhe
- **THEN** as linhas de idade, sexo e raça/cor exibem «—» como os demais campos ausentes do card, sem erro

#### Scenario: Quadro clínico antes do consultivo

- **GIVEN** um caso em `AWAITING_DOCTOR` com procedimentos declarados, sumário clínico, estrutura extraída e alertas consultivos
- **WHEN** o médico abre o detalhe
- **THEN** os títulos aparecem na ordem: Procedimentos do caso → Sumário clínico → Estrutura extraída → Alertas consultivos (o quadro clínico precede o consultivo da automação)

#### Scenario: Detalhe em decisão sem trilha de eventos

- **GIVEN** um caso em `AWAITING_DOCTOR` com trilha de eventos
- **WHEN** o médico abre o detalhe
- **THEN** o card «Trilha de eventos» é renderizado ao final da página, fechado por padrão; ao abrir, cada evento exibe rótulo legível, ator com papel em português e data/hora

#### Scenario: Trilha do médico mostra criação e pós-decisão

- **GIVEN** um caso em `AWAITING_DOCTOR` criado por um NIR, com pipeline concluído
- **WHEN** o médico autorizado abre o detalhe e expande a trilha de eventos
- **THEN** os eventos exibem o NIR como ator da criação, os eventos do pipeline com ator «Sistema» e as datas/horas de cada etapa

#### Scenario: Detalhe médico exibe row detectada não-declarada

- **GIVEN** um caso em `AWAITING_DOCTOR` cuja divergência foi liberada por bypass (declarada `angio_art_perif` não-detectada + `art_perif` detectada não-declarada persistida)
- **WHEN** o médico com especialidade compatível abre o detalhe
- **THEN** o card «Procedimentos do caso» exibe ambas as rows com rótulos legíveis e os badges «Não detectado» e «Detectado na extração»

#### Scenario: Detalhe e decisão usam a referência humana

- **GIVEN** um caso em `AWAITING_DOCTOR` com nº de ocorrência
- **WHEN** o médico abre o detalhe e a página de decisão
- **THEN** `<title>`, breadcrumbs e o link do breadcrumb «Caso …» exibem o nº de ocorrência (`uid <8 chars>` quando o nº está ausente) e o uid completo não aparece em texto visível — apenas nos hrefs

### Requirement: Caso decidido consultável

O sistema SHALL exibir, para `doctor`/`admin` com acesso ao caso (regra de subtipo), o detalhe read-only de casos decididos com as decisões por procedimento, motivos, ator e data — e a trilha de eventos em card collapsible «Trilha de eventos» ao final (a mesma do painel, fechado por padrão) — e sem permitir nova decisão fora de `AWAITING_DOCTOR`.

#### Scenario: Detalhe decidido read-only

- **GIVEN** um caso em `DOCTOR_DENIED`
- **WHEN** o médico autorizado abre o detalhe
- **THEN** as decisões e motivos são exibidos, nenhum formulário de decisão é renderizado e o card «Trilha de eventos» aparece ao final, fechado por padrão
