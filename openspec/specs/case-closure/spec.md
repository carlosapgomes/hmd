# case-closure Specification

## Purpose

Fechar o ciclo do caso após a resposta final — ciência do NIR com disposição de dados clínicos conforme a política de retenção (ADR-0010: retenção temporária na fase de estabilização, minimização reversível por flag e purga retroativa), visão de resultado e de casos encerrados para o remetente, e reenvio corrigido como novo caso vinculado — com trilha auditável e sem quebrar o prior-case.

## Requirements

### Requirement: Resposta final de negativa médica

Quando o médico nega todos os procedimentos declarados, o sistema SHALL publicar automaticamente a resposta final ao NIR na thread do caso — com o motivo de cada procedimento negado — levando o caso a `FINAL_REPLY_POSTED` no mesmo atomic. A resposta SHALL encerrar com a linha do médico responsável pela decisão: `Médico responsável: {nome do médico decisor}` seguido do registro de conselho formatado quando existir (`Médico responsável: {nome} — {conselho} {número}`; ex.: `CRM 12345`); sem registro preenchido, apenas o nome; sem médico decisor registrado, a linha é omitida. Decisão parcial (ao menos um procedimento aprovado) SHALL seguir para o agendamento sem publicar resposta final.

#### Scenario: Negação total publica resposta com motivos

- **GIVEN** um caso em `AWAITING_DOCTOR` com dois procedimentos declarados
- **WHEN** o médico nega ambos informando os motivos
- **THEN** o caso chega a `FINAL_REPLY_POSTED` e a thread contém a resposta final citando cada procedimento negado com seu motivo, encerrada com a última linha `Médico responsável: {nome} — CRM {número}` quando o registro do médico decisor está preenchido

#### Scenario: Negação total com médico sem registro degrada para nome apenas

- **GIVEN** um caso em `AWAITING_DOCTOR` decidido por um médico sem conselho/número preenchidos
- **WHEN** o médico nega todos os procedimentos
- **THEN** a resposta final publicada encerra com `Médico responsável: {nome}` sem registro, e a postagem não falha

#### Scenario: Decisão parcial não publica resposta final

- **GIVEN** um caso em `AWAITING_DOCTOR` com dois procedimentos declarados
- **WHEN** o médico aprova um e nega outro
- **THEN** o caso segue para `SCHEDULER_REQUESTED` e nenhuma resposta final é publicada

#### Scenario: Estado fora da negativa é recusado

- **GIVEN** um caso que não está em `DOCTOR_DENIED`
- **WHEN** o serviço de resposta de negativa é invocado
- **THEN** a operação é recusada com erro nomeado e nada muda

### Requirement: Ciência do NIR com encerramento e disposição de dados

Qualquer usuário com papel ativo `nir` — criador ou não (equipe NIR) — SHALL poder
confirmar o recebimento da resposta final, com o operador registrado como ator
do evento,
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

- **GIVEN** um caso em `FINAL_REPLY_POSTED`, com documentos, artefatos do pipeline e mapa de pseudônimos, e o sistema em modo de minimização
- **WHEN** um NIR com papel ativo (criador ou não) confirma o recebimento
- **THEN** o caso chega a `CLEANED`, os documentos deixam de existir (rows e acesso), e texto extraído/anonimizado, mapa de pseudônimos e artefatos do pipeline estão vazios

#### Scenario: Ciência com retenção preserva tudo

- **GIVEN** o mesmo caso com o sistema em modo de retenção (`CASE_CLOSURE_MINIMIZATION_ENABLED` desativado — default da fase de estabilização)
- **WHEN** um NIR com papel ativo (criador ou não) confirma o recebimento
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
- **WHEN** um NIR com papel ativo (criador ou não) confirma o recebimento
- **THEN** em modo de minimização as rows e os arquivos dos anexos deixam de existir, junto com os documentos e artefatos do caso; em modo de retenção permanecem intactos

#### Scenario: Ciência por NIR não-criador da equipe

- **GIVEN** um caso em `FINAL_REPLY_POSTED` criado por outro usuário NIR
- **WHEN** um NIR diferente confirma o recebimento
- **THEN** o caso chega a `CLEANED` normalmente e o evento de ciência registra o NIR operador como ator
### Requirement: Resultado e casos encerrados visíveis ao criador

O detalhe do caso para o NIR SHALL exibir, a partir da decisão médica, o
resultado (decisões por procedimento com motivos, dados de agendamento e a
resposta final em destaque) e um meio de confirmar o recebimento apenas
enquanto o caso estiver em `FINAL_REPLY_POSTED` — **acessível a qualquer
usuário com papel ativo `nir`, de casos de qualquer criador (equipe)**. A lista
«Casos em Processamento» SHALL listar **apenas casos ativos** (tudo exceto
`CLEANED`) de qualquer criador —
sem navegação Ativos/Encerrados; casos encerrados são alcançados pela página «Casos Encerrados» (busca global) e seu
detalhe histórico, onde o encerramento ADMINISTRATIVO também aparece com o
resultado "Encerrado administrativamente" e o motivo registrado.

#### Scenario: Resultado exibido ao criador

- **GIVEN** um caso com resposta final publicada, criado por qualquer NIR
- **WHEN** um usuário com papel ativo `nir` (criador ou não) abre o detalhe do caso
- **THEN** decisões com motivos, dados de agendamento e a resposta final são exibidos, junto ao botão de confirmação de recebimento

#### Scenario: Ciência apenas do criador no estado certo

- **GIVEN** um caso em `FINAL_REPLY_POSTED` (de qualquer criador) e um caso já encerrado (`CLEANED`)
- **WHEN** um NIR com papel ativo tenta confirmar recebimento de cada um deles
- **THEN** a ciência é aceita no caso em `FINAL_REPLY_POSTED` (criador ou não) e recusada com mensagem de erro no caso já encerrado, sem mudança de estado indevida

#### Scenario: Aba de encerrados lista apenas casos CLEANED

- **GIVEN** 2 casos ativos e 1 encerrado (`CLEANED`) no universo da equipe
- **WHEN** o NIR abre «Casos em Processamento»
- **THEN** apenas os 2 casos ativos são listados (o `CLEANED` não aparece; não existe mais navegação Ativos/Encerrados e o parâmetro legado `?tab=closed` cai na listagem de ativos preservando os filtros) e o caso encerrado é alcançável pela página «Casos Encerrados» (busca), que lista apenas `CLEANED` e intercorrências ativas

#### Scenario: Encerrado do criador alcançável pela busca

- **GIVEN** um caso `CLEANED` de qualquer criador
- **WHEN** o NIR busca pelo nº na página «Casos Encerrados» e abre o detalhe histórico
- **THEN** o resultado (decisões, agendamento, resposta final, comunicações e trilha) é exibido em leitura; o redirect pós-ciência leva o NIR operador ao detalhe do caso

#### Scenario: Encerramento administrativo visível ao criador

- **GIVEN** um caso encerrado administrativamente
- **WHEN** o NIR o localiza pela página «Casos Encerrados» e abre o detalhe histórico
- **THEN** o caso aparece com o resultado "Encerrado administrativamente" e o motivo registrado
### Requirement: Reenvio corrigido cria novo caso vinculado

O sistema SHALL permitir a **qualquer usuário com papel ativo `nir`** (criador ou não) reenviar, de forma corrigida, um caso encerrado: cria um novo caso que percorre o pipeline completo desde o início, vinculado ao original com motivo obrigatório e exatamente 1 PDF de relatório + um único tipo declarado explicitamente (nunca herdado; múltiplos tipos e múltiplos arquivos são rejeitados com erro nomeado, sem criar o caso). O usuário reenviador SHALL ser o criador do novo caso (`created_by`/`correction_created_by`), recebendo as notificações do novo ciclo (a ciência é da equipe NIR — ver requisito «Ciência do NIR com encerramento e disposição de dados» desta change). O caso original SHALL permanecer inalterado exceto pelo evento de supersedição e pela listagem dos reenvios que o corrigem. A ação «Reenviar corrigido» SHALL estar disponível no detalhe histórico de casos `CLEANED` (qualquer criador) e no detalhe encerrado; recusa para caso fora de `CLEANED`, sem qualquer escrita. A navegação da página de reenvio (breadcrumb, cancelamento e redirects de recusa) SHALL apontar ao detalhe histórico do original — acessível a qualquer papel `nir` —, nunca a rotas fora do alcance do papel `nir`; o redirect de sucesso leva o reenviador ao detalhe do novo caso (seu). Na listagem «Reenvios corrigidos» do caso original, o link para o caso corrigido SHALL levar ao detalhe do caso quando ativo ou ao detalhe histórico quando encerrado — ambos acessíveis a qualquer `nir` —, sempre com a referência desambiguada quando o nº é duplicado.

#### Scenario: Reenvio cria novo caso vinculado com motivo

- **GIVEN** um caso encerrado
- **WHEN** um usuário com papel ativo `nir` (criador ou não) o reenvia de forma corrigida com exatamente 1 PDF novo, tipo declarado e motivo
- **THEN** um novo caso é criado em processamento inicial vinculado ao original, ambos registram os eventos de correção e supersedição, e o reenviador é o criador do novo caso

#### Scenario: Tipos do reenvio não são herdados

- **GIVEN** um caso encerrado cujo tipo original é um subtipo qualquer
- **WHEN** o NIR reenvia declarando um tipo diferente do original
- **THEN** o novo caso registra exatamente o tipo declarado no reenvio

#### Scenario: Original permanece encerrado e íntegro

- **GIVEN** um caso encerrado que sofreu um reenvio corrigido
- **WHEN** o original é consultado
- **THEN** permanece `CLEANED` com seus dados intactos, listando o reenvio que o corrige

#### Scenario: Reenvio exige caso encerrado próprio

- **GIVEN** um caso não encerrado e um caso encerrado criado por outro NIR
- **WHEN** o NIR autenticado tenta o reenvio corrigido de qualquer um deles
- **THEN** o não encerrado é recusado com erro nomeado e nenhum caso é criado; o encerrado de outro criador é aceito (guarda por papel, não por posse — decisão do dono 2026-11-21)

#### Scenario: Reenvio com múltiplos arquivos ou tipos é rejeitado

- **GIVEN** um caso encerrado
- **WHEN** o NIR o reenvia de forma corrigida com mais de 1 PDF ou mais de um tipo declarado
- **THEN** nenhum caso é criado e o erro nomeado informa as restrições

#### Scenario: Navegação da página de reenvio para o não-criador

- **GIVEN** um NIR não-criador no formulário de reenvio de um caso `CLEANED` alheio, aberto a partir do detalhe histórico
- **WHEN** ele usa o breadcrumb ou «Cancelar», ou sofre recusa (estado incorreto / intake desabilitado)
- **THEN** todos os destinos são o detalhe histórico do original (detalhe do caso ativo — alcançável por qualquer `nir`), sem qualquer 404 de rota restrita ao criador; no sucesso, o redirect leva ao detalhe do novo caso, de sua autoria

#### Scenario: Link do caso corrigido respeita a autorização do visitante

- **GIVEN** um caso encerrado com reenvio corrigido criado por outro NIR
- **WHEN** qualquer NIR vê a listagem «Reenvios corrigidos deste caso»
- **THEN** enquanto o novo caso está ativo, a referência desambiguada aponta ao detalhe do caso; quando o novo caso encerra (`CLEANED`), a referência passa a apontar ao detalhe histórico — para qualquer visitante `nir`, criador ou não
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

### Requirement: Purga retroativa dos dados retidos

O sistema SHALL oferecer comando de gerência que reaplica minimização a CLEANED com dados retidos, zerando campos clínicos/LLM e removendo rows e arquivos de documentos/anexos. SHALL registrar auditoria por caso com nomes de campos e contagens, nunca conteúdo clínico ou nomes de arquivos; ser idempotente; suportar --dry-run; reportar falha física por caso e sair com erro se houver. EXCEÇÃO: incoming de reapresentação vinculado a canônico ainda não CLEANED SHALL ser protegido e reportado como tal no dry-run e execução real. Após encerramento do canônico, a purga SHALL alcançar o grupo sem permitir corrida com reabertura. Pedido de purga de incoming cujo canônico CLEANED ainda não foi disposto SHALL purgar o grupo inteiro ou recusar sem escrita; nunca apagar só incoming enquanto canônico continua reabrível. Dry-run SHALL agrupar e deduplicar a mesma unidade de disposição.

#### Scenario: Purga minimiza casos encerrados retidos
- **GIVEN** casos CLEANED retidos sem proteção ativa
- **WHEN** comando executa
- **THEN** campos são zerados, rows/arquivos removidos, auditoria guarda nomes de campos/contagens sem conteúdo e rotas de documento retornam 404

#### Scenario: Falha na deleção física é reportada
- **WHEN** arquivo falha ao ser removido
- **THEN** caso é reportado incompleto com contagem de falhas e comando sai com erro

#### Scenario: Casos ativos não são tocados
- **WHEN** purga executa com casos operacionais presentes
- **THEN** nenhum caso fora de CLEANED é alterado

#### Scenario: Purga é idempotente
- **WHEN** comando executa novamente sobre casos já purgados
- **THEN** não há nova alteração nem eventos duplicados

#### Scenario: Execução a seco não altera nada
- **WHEN** comando executa com --dry-run
- **THEN** relatório lista elegíveis e protegidos sem alterar dados

#### Scenario: Incoming encerrado protegido por canônico ativo
- **WHEN** comando encontra incoming CLEANED vinculado a canônico ativo, inclusive com resposta final ainda sem ciência
- **THEN** informa proteção e não apaga dados ou arquivos daquele incoming

#### Scenario: Purga de incoming com canônico ainda reabrível
- **WHEN** purga é solicitada para incoming e canônico está CLEANED com dados retidos e sem disposição marcada
- **THEN** operação dispõe grupo inteiro e marca canônico atomicamente, ou não remove nada se inelegível; não deixa canônico reabrível com documentação recebida removida

#### Scenario: Reabertura concorrente
- **WHEN** canônico é reaberto enquanto purga tenta avaliar o grupo
- **THEN** a proteção é revalidada sob exclusão mútua e não ocorre purga de grupo com ciclo operacional ativo

### Requirement: Encerramento específico por reapresentação

O sistema SHALL encerrar por reapresentação somente após resolução válida do mesmo pedido, com vínculo e motivo rastreáveis. Esse encerramento SHALL ser distinto de ciência NIR, negativa clínica e encerramento administrativo; não exige simular papel manager/admin nem emite decisão ou resposta final fictícia.

#### Scenario: Encerramento sistêmico legítimo
- **WHEN** resolução médica ou de agendamento é aplicada
- **THEN** caso apropriado chega a CLEANED com vínculo e evento próprio e caso mantido continua operacional

#### Scenario: Disposição depende de quem encerra
- **WHEN** original médico é substituído ou incoming é arquivado no agendamento
- **THEN** original médico segue modo ordinário de retenção/minimização; incoming do agendamento retém dados e marcador de disposição nulo até disposição do grupo, sem apagar arquivos antecipadamente

#### Scenario: Resultado legível
- **WHEN** NIR consulta caso encerrado por reapresentação na busca/detalhe histórico
- **THEN** vê o motivo, elo imediato e link autorizado ao representante atual da cadeia, sem reescrever elos antigos ou apresentar conclusão clínica/negativa inexistente

### Requirement: Proteção dos reenvios vinculados ao ciclo ativo

Documentos e anexos de incoming encerrado por reapresentação de agendamento SHALL permanecer disponíveis enquanto canônico não estiver CLEANED, inclusive em minimização. No encerramento canônico por ciência ou administração, os incoming vinculados SHALL seguir a mesma política de disposição do ciclo: minimização remove dados/arquivos e retenção preserva. Identidade, decisões e auditoria permanecem.

#### Scenario: Minimização não apaga documentação de fila ativa
- **WHEN** incoming encerra vinculado a original ainda aguardando agendamento com minimização ativa
- **THEN** seus relatórios e anexos continuam acessíveis pelas rotas autorizadas

#### Scenario: Encerramento do ciclo minimiza grupo
- **WHEN** canônico encerra por ciência ou administração em minimização
- **THEN** disposição inclui dados clínicos/documentos/anexos dos incoming vinculados, com remoção física após commit

#### Scenario: Retenção preserva grupo
- **WHEN** canônico encerra sob retenção
- **THEN** dados do canônico e incoming permanecem e só purga explícita posterior pode minimizá-los
