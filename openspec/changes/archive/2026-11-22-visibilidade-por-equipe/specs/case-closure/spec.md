# Specification (delta)

## Purpose

Resultado final e encerramento do ciclo em escopo de equipe: qualquer `nir` visualiza o resultado e registra a ciência, não apenas o criador.

## MODIFIED Requirements

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
