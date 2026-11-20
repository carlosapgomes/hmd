# Spec Delta

## ADDED Requirements

### Requirement: Confirmação humana de procedimento com proveniência

A confirmação do NIR SHALL ser auditável, versionada e vinculada ao ator, papel, revisão, fonte principal, texto anonimizado e mapa de pseudônimos vigentes. O registro SHALL distinguir seleção anterior, seleção confirmada, manutenção ou troca e justificativa. Aplicação e invalidação SHALL preservar a história automática e humana, sem converter declaração ou bypass antigo em confirmação. Uma confirmação invalidada não SHALL recuperar autoridade.

#### Scenario: Manutenção é registrada como confirmação real
- **GIVEN** caso novo retido com um procedimento declarado
- **WHEN** o NIR confirma o mesmo procedimento com os campos obrigatórios
- **THEN** a auditoria identifica autor, papel, instante, revisão, fonte, justificativa e seleção mantida, sem simular uma troca

#### Scenario: Troca preserva declaração anterior e detecção
- **GIVEN** um caso novo retido com procedimento A declarado e B detectado
- **WHEN** o NIR confirma C do catálogo
- **THEN** C é a declaração corrente única, a história mantém A e B e a confirmação não transforma C em detecção automática

#### Scenario: Confirmação perde validade por fonte ou revisão alterada
- **GIVEN** uma confirmação anteriormente válida
- **WHEN** a fonte principal, texto anonimizado, mapa de pseudônimos, declaração ou revisão de origem muda
- **THEN** a confirmação deixa de autorizar a análise, a invalidação fica auditada sem duplicação equivalente e nenhuma confirmação anterior é recuperada; no worker, inconsistência de fonte/anonimização resulta em FAILED operacional sem novo dispatch, enquanto revisão/declaração alterada com integridade preservada permite reconciliação automática

#### Scenario: Restauração dos valores não revive confirmação invalidada
- **GIVEN** confirmação invalidada após mudança de declaração ou revisão
- **WHEN** os valores e digests voltam a coincidir com os originais e o worker revalida
- **THEN** o evento continua sem autoridade, nenhuma confirmação antiga o substitui e não é criada invalidação duplicada apenas por essa revalidação; nova autoridade humana exige outra confirmação explícita da revisão vigente

#### Scenario: Auditoria legível da aplicação
- **GIVEN** confirmação aplicada ao processamento
- **WHEN** o supervisor consulta a trilha autorizada
- **THEN** encontra confirmação e aplicação correlacionadas, seleção mantida ou trocada, justificativa e diferença entre automático e efetivo, sem apagar eventos anteriores

### Requirement: Reprocessamento do mesmo caso após revisão de procedimento

O sistema SHALL oferecer operação protegida de reprocessamento da revisão de divergência, sem estado novo, mantendo o mesmo caso e suas fontes. Confirmação, declaração, invalidação dos derivados e pedido de reprocessamento SHALL ser atômicos. A operação SHALL exigir autorização e exclusividade de mutação; o processamento externo só inicia após commit.

#### Scenario: Reprocessamento mantém identidade e fontes
- **GIVEN** um caso novo retido em `LLM_EXTRACTING` sem decisão médica
- **WHEN** a confirmação válida solicita reprocessamento
- **THEN** mantém o identificador e retorna à extração LLM, preservando PDF, anexos, texto extraído, anonimização, metadados e histórico; resumo, estrutura, policy e sugestões anteriores não são reutilizados como resultado novo

#### Scenario: Erro desfaz a confirmação inteira
- **GIVEN** confirmação com troca de procedimento
- **WHEN** falha a persistência antes do commit
- **THEN** declaração, flags, derivados, confirmação e pedido de reprocessamento permanecem como antes e nenhum job é disparado

#### Scenario: Submissões concorrentes têm um vencedor
- **GIVEN** duas submissões da mesma revisão
- **WHEN** ambas tentam confirmar
- **THEN** somente uma registra confirmação e pedido de reprocessamento; a outra recebe erro de revisão não mais disponível sem efeitos parciais

#### Scenario: Caso sob processamento ou já decidido não é confirmado
- **GIVEN** caso com lease ativa de outro ator, decisão médica ou estado fora da revisão
- **WHEN** é solicitada confirmação
- **THEN** a solicitação é recusada sem alterar fonte, decisão, declaração ou resultados
