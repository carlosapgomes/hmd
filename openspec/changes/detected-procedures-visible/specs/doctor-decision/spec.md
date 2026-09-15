## MODIFIED Requirements

### Requirement: Presenter re-identificado sob papel autorizado
O detalhe do caso SHALL apresentar, apenas para `doctor`/`admin`, os dados reais do paciente (identificação, número de ocorrência) e a demografia do caso — idade, sexo e raça/cor extraídas do cabeçalho padrão SESAB — e os artefatos do pipeline re-identificados: histórico e sumário, estrutura extraída, alertas da policy com a recomendação por procedimento e o resultado agregado, requisitos gerais acionáveis e o PDF original. A re-identificação SHALL ocorrer apenas na renderização para papel autorizado; nenhum artefato re-identificado é persistido nem enviado a qualquer LLM. A ordem dos cards SHALL seguir a leitura clínica: identificação, «Procedimentos do caso», sumário clínico e estrutura extraída (o quadro clínico) ANTES dos alertas consultivos e demais cards da automação. O detalhe NÃO exibe a trilha de eventos (vive no painel). O card «Procedimentos do caso» exibe TODAS as rows — inclusive a detectada não-declarada que sobrevive ao bypass da divergência — com badges de origem (Declarado/Detectado na extração) e de detecção após a reconciliação.
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
- **THEN** nenhum bloco de trilha de eventos é renderizado

#### Scenario: Detalhe médico exibe row detectada não-declarada

- **GIVEN** um caso em `AWAITING_DOCTOR` cuja divergência foi liberada por bypass (declarada `angio_art_perif` não-detectada + `art_perif` detectada não-declarada persistida)
- **WHEN** o médico com especialidade compatível abre o detalhe
- **THEN** o card «Procedimentos do caso» exibe ambas as rows com rótulos legíveis e os badges «Não detectado» e «Detectado na extração»
