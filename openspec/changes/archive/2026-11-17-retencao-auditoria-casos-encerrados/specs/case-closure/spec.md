# Spec Delta

## RENAMED Requirements

- FROM: `### Requirement: Ciência do NIR com limpeza de dados clínicos`
  TO: `### Requirement: Ciência do NIR com encerramento e disposição de dados`

## MODIFIED Requirements

### Requirement: Ciência do NIR com encerramento e disposição de dados

O criador do caso SHALL poder confirmar o recebimento da resposta final,
levando o caso de `FINAL_REPLY_POSTED` a `CLEANED` de forma transacional,
com a disposição dos dados clínicos governada pelo modo operacional
(`CASE_CLOSURE_MINIMIZATION_ENABLED`):

- **minimização ativa** (`true`): documentos originais e anexos (rows e
  arquivos físicos), texto extraído, texto anonimizado, mapa de
  pseudônimos e artefatos do pipeline (estrutura, sumário, sugestão,
  policy) são removidos — inclusive o conteúdo e os mapas dos anexos;
- **retenção ativa** (`false`, modo da fase de desenvolvimento/
  estabilização): nada é removido — os campos clínicos/LLM, as rows e os
  arquivos de documentos e anexos permanecem intactos e acessíveis pelas
  rotas de visualização dos papéis autorizados.

Em ambos os modos, identificação do paciente, decisões médicas, trilha de
eventos, comunicações e dados de agendamento são preservados, e um caso
encerrado dentro da janela SHALL continuar elegível como prior-case. O
modo de retenção é política temporária (benchmark + auditoria) e sua
reversão opera pelo mesmo flag e pela purga retroativa.

#### Scenario: Ciência encerra e limpa o caso

- **GIVEN** um caso em `FINAL_REPLY_POSTED` criado pelo NIR autenticado, com documentos, artefatos do pipeline e mapa de pseudônimos, e o sistema em modo de minimização
- **WHEN** ele confirma o recebimento
- **THEN** o caso chega a `CLEANED`, os documentos deixam de existir (rows e acesso), e texto extraído/anonimizado, mapa de pseudônimos e artefatos do pipeline estão vazios

#### Scenario: Ciência com retenção preserva tudo

- **GIVEN** o mesmo caso com o sistema em modo de retenção (`CASE_CLOSURE_MINIMIZATION_ENABLED` desativado — default da fase de estabilização)
- **WHEN** ele confirma o recebimento
- **THEN** o caso chega a `CLEANED` mantendo documentos e anexos (rows e arquivos físicos), texto extraído/anonimizado, mapa de pseudônimos e artefatos do pipeline intactos e acessíveis

#### Scenario: Essenciais preservados após a limpeza

- **GIVEN** um caso recém-encerrado pela ciência do NIR em qualquer modo
- **WHEN** o detalhe do caso é consultado
- **THEN** identificação do paciente, decisões médicas por procedimento, trilha de eventos, comunicações e dados de agendamento continuam presentes

#### Scenario: Caso encerrado segue elegível como prior-case

- **GIVEN** um caso encerrado (`CLEANED`) cuja decisão médica mais recente foi registrada dentro da janela do prior-case
- **WHEN** um novo caso do mesmo tipo/paciente consulta o contexto de casos anteriores
- **THEN** o caso encerrado é considerado pelo prior-case com suas decisões

#### Scenario: Documento de caso encerrado é inacessível

- **GIVEN** um caso encerrado pela ciência
- **WHEN** qualquer rota de documento do caso é acessada
- **THEN** retorna HTTP 404 em modo de minimização; retorna HTTP 200 com o arquivo em modo de retenção

#### Scenario: Estado fora da ciência é recusado

- **GIVEN** um caso que não está em `FINAL_REPLY_POSTED`
- **WHEN** a ciência é invocada
- **THEN** a operação é recusada com erro nomeado e nada muda

#### Scenario: Anexos são removidos na ciência

- **GIVEN** um caso em `FINAL_REPLY_POSTED` com anexos processados (arquivos, textos extraídos/anonimizados e mapas de pseudônimos dos anexos)
- **WHEN** o criador confirma o recebimento
- **THEN** em modo de minimização as rows e os arquivos dos anexos deixam de existir, junto com os documentos e artefatos do caso; em modo de retenção permanecem intactos

### Requirement: Encerramento administrativo do caso

O sistema SHALL oferecer transição excepcional de qualquer status diferente
de `CLEANED` para `CLEANED` (encerramento administrativo), restrita às
interfaces do painel (`manager`/`admin`), exigindo código de motivo do
catálogo fixo (`processing_error`, `llm_failure`, `system_bug`,
`stuck_lock`, `duplicate_reprocess`, `other`) e texto descritivo
obrigatório. O encerramento SHALL registrar evento de auditoria
`CASE_ADMINISTRATIVELY_CLOSED` com código, texto, autor e papel; SHALL
limpar o lock operacional persistido do caso; SHALL dispor dos dados
clínicos conforme o modo operacional vigente (`CASE_CLOSURE_MINIMIZATION_ENABLED`):
minimização ativa remove documentos e anexos (rows e arquivos) e zera os
campos clínicos; retenção ativa preserva tudo (mesma disposição do
encerramento por ciência). SHALL notificar o criador do caso; e SHALL ser
recusado enquanto houver lock de worker com lease válida. Casos já em
`CLEANED` SHALL ser rejeitados.

#### Scenario: Supervisor encerra caso travado com motivo

- **GIVEN** um caso em `FAILED` e um usuário com papel ativo `manager`
- **WHEN** encerra administrativamente com código `processing_error` e texto
- **THEN** o caso vai a `CLEANED`, o evento de auditoria é registrado com o
  payload completo e o lock do caso fica limpo

#### Scenario: Encerramento minimiza dados clínicos

- **GIVEN** um caso com documentos, anexos e campos clínicos preenchidos
- **WHEN** é encerrado administrativamente
- **THEN** em modo de retenção documentos, anexos e campos clínicos permanecem intactos e acessíveis; em modo de minimização documentos e anexos são removidos (rows e arquivos físicos do storage), os campos clínicos ficam zerados e o download de documento do caso não é mais possível

#### Scenario: Encerramento recusado durante processamento ativo

- **GIVEN** um caso com lock de worker e lease ainda válida
- **WHEN** tenta-se o encerramento administrativo
- **THEN** a operação é recusada com erro e o caso permanece inalterado

#### Scenario: Worker retornado após encerramento não repopula dados

- **GIVEN** um caso com lease de worker EXPIRADA, encerrado
  administrativamente
- **WHEN** um passo em voo (task de processamento — inclusive o worker de
  anexos, em qualquer etapa, até o dispatch de OCR externo — ou passo do
  pipeline) tenta executar ou persistir resultados clínicos
- **THEN** a execução é abortada/bloqueada pelo status `CLEANED` e os dados
  clínicos do caso permanecem exatamente como estavam no encerramento

#### Scenario: Encerramento exige texto do motivo

- **GIVEN** um caso ativo e o formulário de encerramento sem texto
- **WHEN** o encerramento é submetido
- **THEN** é rejeitado com erro de validação e o caso permanece no estado
  anterior

#### Scenario: Motivo fora do catálogo é rejeitado

- **GIVEN** o formulário de encerramento com código inexistente no catálogo
- **WHEN** o encerramento é submetido
- **THEN** é rejeitado com erro de validação

#### Scenario: Caso já concluído não é encerrável novamente

- **GIVEN** um caso já em `CLEANED`
- **WHEN** tenta-se o encerramento administrativo
- **THEN** a operação é rejeitada com erro

#### Scenario: Criador é notificado do encerramento

- **GIVEN** um caso encerrado administrativamente
- **WHEN** o criador consulta suas notificações
- **THEN** existe notificação apontando para o caso com o marco de
  encerramento administrativo

#### Scenario: Papel fora de manager/admin não encerra

- **GIVEN** um usuário com papel ativo `nir`, `doctor` ou `scheduler`
- **WHEN** tenta submeter o encerramento administrativo
- **THEN** recebe 403

## ADDED Requirements

### Requirement: Purga retroativa dos dados retidos

O sistema SHALL oferecer comando de gerência que reaplica a minimização
aos casos `CLEANED` cujos dados clínicos foram retidos pela política
temporária: zera os campos clínicos/LLM e remove rows e arquivos físicos
de documentos e anexos, com a mesma semântica da disposição minimizante do
encerramento. O comando SHALL tocar apenas casos `CLEANED` com dados
retidos, SHALL registrar ocorrência auditável na trilha de eventos de cada
caso purgado — cujo payload SHALL conter apenas nomes de campos e
contagens, nunca conteúdo clínico nem nomes de arquivo —, SHALL ser
idempotente (re-execução sobre casos já purgados é no-op) e SHALL suportar
execução a seco (`--dry-run`) que reporta o que seria purgado sem alterar
nada. Falha na deleção física de arquivo SHALL ser reportada por caso
(contagens de arquivos tentados/removidos/falhados) e o comando SHALL
sinalizar a purga do caso como incompleta, encerrando com saída de erro
quando houver falha física.

#### Scenario: Purga minimiza casos encerrados retidos

- **GIVEN** casos `CLEANED` encerrados em modo de retenção, com documentos, anexos e artefatos presentes
- **WHEN** o comando de purga é executado
- **THEN** os campos clínicos ficam zerados, documentos e anexos deixam de existir (rows e arquivos), a trilha de cada caso registra a purga com payload de nomes de campos e contagens (sem conteúdo clínico nem nomes de arquivo) e rotas de documento passam a retornar 404

#### Scenario: Falha na deleção física é reportada

- **GIVEN** um caso `CLEANED` retido cujo arquivo físico falha ao ser removido
- **WHEN** o comando de purga é executado
- **THEN** o caso é reportado com purga incompleta (contagem de arquivos falhados) e o comando encerra com saída de erro listando o caso

#### Scenario: Casos ativos não são tocados

- **GIVEN** casos em estados operacionais com dados presentes
- **WHEN** o comando de purga é executado
- **THEN** nenhum caso fora de `CLEANED` é alterado

#### Scenario: Purga é idempotente

- **GIVEN** casos `CLEANED` já purgados
- **WHEN** o comando de purga é executado novamente
- **THEN** é no-op sobre esses casos, sem eventos duplicados

#### Scenario: Execução a seco não altera nada

- **GIVEN** casos `CLEANED` com dados retidos
- **WHEN** o comando é executado com `--dry-run`
- **THEN** o relatório lista os casos que seriam purgados e nenhum dado é alterado
