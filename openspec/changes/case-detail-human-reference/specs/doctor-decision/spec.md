# Delta: doctor-decision

## MODIFIED Requirements

### Requirement: Presenter re-identificado sob papel autorizado

O detalhe do caso SHALL apresentar, apenas para `doctor`/`admin`, os dados reais do paciente (identificação, número de ocorrência) e a demografia do caso — idade, sexo e raça/cor extraídas do cabeçalho padrão SESAB — e os artefatos do pipeline re-identificados: histórico e sumário, estrutura extraída, alertas da policy com a recomendação por procedimento e o resultado agregado, requisitos gerais acionáveis e o PDF original. A re-identificação SHALL ocorrer apenas na renderização para papel autorizado; nenhum artefato re-identificado é persistido nem enviado a qualquer LLM. A ordem dos cards SHALL seguir a leitura clínica: identificação, «Procedimentos do caso», sumário clínico e estrutura extraída (o quadro clínico) ANTES dos alertas consultivos e demais cards da automação. O detalhe NÃO exibe a trilha de eventos (vive no painel). O card «Procedimentos do caso» exibe TODAS as rows — inclusive a detectada não-declarada que sobrevive ao bypass da divergência — com badges de origem (Declarado/Detectado na extração) e de detecção após a reconciliação. O `<title>`, o breadcrumb e os links de navegação entre detalhe e decisão SHALL usar a **referência humana** do caso — nº de ocorrência com fallback `uid <8 primeiros chars>` (padrão do painel) — e o uid completo NÃO aparece em texto visível nessas superfícies (permanece apenas como chave das URLs).

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

#### Scenario: Detalhe e decisão usam a referência humana

- **GIVEN** um caso em `AWAITING_DOCTOR` com nº de ocorrência
- **WHEN** o médico abre o detalhe e a página de decisão
- **THEN** `<title>`, breadcrumbs e o link do breadcrumb «Caso …» exibem o nº de ocorrência (`uid <8 chars>` quando o nº está ausente) e o uid completo não aparece em texto visível — apenas nos hrefs

### Requirement: Card de prior-case com motivo real

O detalhe do caso SHALL exibir, por procedimento declarado com caso prévio
encontrado (número de ocorrência ou fallback nome+nascimento), um card com
data, desfecho e motivo real da decisão prévia. O badge do caso prévio SHALL
exibir a **referência desambiguada** do prévio — `nº · <8 primeiros chars do
uid>` (ou `uid <8 chars>` quando sem nº) — e não o uid completo. O lookup do
presenter é somente-leitura (eventos de lookup permanecem os do pipeline) e
respeita as mesmas janelas configuráveis do pipeline.

#### Scenario: Card de negativa prévia por número de ocorrência

- **GIVEN** um caso cujo procedimento tem caso prévio negado há 3 dias com o mesmo número de ocorrência
- **WHEN** o médico abre o detalhe
- **THEN** o card exibe data, desfecho negado e o motivo real registrado pelo médico prévio

#### Scenario: PDF original restrito a papel autorizado

- **GIVEN** um caso com documento PDF
- **WHEN** um usuário com papel ativo `nir` tenta acessar a URL do PDF médico diretamente
- **THEN** recebe HTTP 403 sem conteúdo do arquivo

#### Scenario: Card de prior-case por fallback nome+nascimento

- **GIVEN** um caso cujo procedimento tem caso prévio negado há 10 dias, número de ocorrência diferente, mesmo nome normalizado e nascimento
- **WHEN** o médico abre o detalhe
- **THEN** o card exibe o prévio com desfecho e motivo real, marcando a origem do match

#### Scenario: Badge do prior-case usa referência desambiguada

- **GIVEN** um caso cujo procedimento tem caso prévio com nº de ocorrência N (mesmo nº do caso corrente, originado de reenvio corrigido)
- **WHEN** o médico abre o detalhe
- **THEN** o badge do prior-case exibe `N · <8 primeiros chars do uid do prévio>` — o uid completo do prévio não aparece em texto visível
