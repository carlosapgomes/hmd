# Delta: attachments

## MODIFIED Requirements

### Requirement: Upload e listagem de anexos pelo NIR

O sistema SHALL permitir ao NIR anexar arquivos (JPEG, PNG ou PDF) no momento da criação do caso e no reenvio corrigido, respeitando limites de contagem e tamanho por arquivo, com rejeição nomeada antes de qualquer gravação; anexos são aceitos SOMENTE quando o envio contém exatamente 1 PDF de relatório (com múltiplos PDFs a vinculação seguro entre relatórios e anexos é impossível) — nesse caso os casos do lote são criados sem anexos e o erro informa a restrição; com exatamente 1 PDF, anexo inválido aborta o envio inteiro. Anexos aceitos ficam listados no detalhe do caso para o criador com nome, status de processamento legível e **visualização do arquivo** (link de abertura + embed/imagem colapsável, padrão `pdf-original-viewer`), servida por rota própria do NIR sob o guard de criador. O caso sem anexos SHALL seguir idêntico ao fluxo atual.

#### Scenario: Anexos criados junto com o caso

- **GIVEN** um upload de caso com exatamente 1 PDF de relatório e dois anexos (uma foto JPEG e um PDF)
- **WHEN** a criação é submetida
- **THEN** o caso é criado com seu documento e as rows de anexo com os arquivos gravados, ambos na mesma transação

#### Scenario: Anexo fora dos limites é rejeitado sem efeito

- **GIVEN** um upload com exatamente 1 PDF de relatório e um anexo de tipo não aceito ou acima do limite de tamanho/contagem
- **WHEN** a criação é submetida
- **THEN** a validação falha com erro nomeado e nada é gravado (nem caso, nem documentos)

#### Scenario: NIR vê seus anexos com status

- **GIVEN** um caso do NIR com um anexo já processado e outro pendente
- **WHEN** ele abre o detalhe do caso
- **THEN** ambos os anexos são listados com nome e status legível de processamento

#### Scenario: Anexos com múltiplos relatórios são recusados sem bloquear

- **GIVEN** um upload com 2 PDFs de relatório válidos e dois anexos
- **WHEN** a criação é submetida
- **THEN** os dois casos são criados sem anexos e o erro nomeado informa que anexos exigem exatamente 1 relatório

#### Scenario: NIR visualiza o arquivo do anexo

- **GIVEN** um caso do NIR com um anexo PDF e um JPEG
- **WHEN** ele abre o detalhe do caso
- **THEN** cada anexo tem link de abertura do arquivo e visualização colapsável (embed lazy para PDF, imagem lazy para JPEG); anexo de caso alheio responde 404

### Requirement: Anexos na decisão médica

A tela de decisão médica SHALL exibir, quando houver anexos, um card por anexo com status, método, resultado da verificação e resumo/evidência re-identificados apenas na renderização, acrescido da **visualização do arquivo** (embed lazy para PDF, imagem lazy para JPEG/PNG, link mobile para viewer dedicado — padrão `pdf-original-viewer`); divergência (`mismatch`) SHALL aparecer como alerta consultivo sem nunca descartar ou bloquear o caso. Casos sem anexos permanecem visualmente idênticos.

#### Scenario: Card re-identificado na decisão

- **GIVEN** um caso em avaliação médica com um anexo processado com `match`
- **WHEN** o médico abre o detalhe do caso
- **THEN** o card do anexo exibe o resultado e o resumo com dados reais (sem tokens na página)

#### Scenario: Mismatch é alerta sem bloqueio

- **GIVEN** um caso em avaliação médica com um anexo verificado como `mismatch`
- **WHEN** o médico abre o detalhe
- **THEN** o card exibe alerta de divergência de identificação e o caso permanece decidível normalmente

#### Scenario: Anexo pendente ou falho aparece com status

- **GIVEN** um caso em avaliação médica com um anexo ainda pendente e outro falho
- **WHEN** o médico abre o detalhe
- **THEN** ambos aparecem com seus status legíveis, sem resultado de verificação

#### Scenario: Card do anexo exibe o arquivo

- **GIVEN** um caso em avaliação médica com um anexo PDF processado
- **WHEN** o médico abre o detalhe
- **THEN** o card do anexo inclui a visualização colapsável do arquivo via rota de serve do papel; médico sem acesso ao caso recebe 404 no serve

## ADDED Requirements

### Requirement: Anexos visualizáveis no painel

O detalhe do caso no painel (manager/admin) SHALL listar os anexos do caso com nome, status e **visualização do arquivo** (link de abertura + embed/imagem colapsável, padrão `pdf-original-viewer`), servida por rota própria do painel sob o guard `manager`/`admin`. Casos sem anexos não exibem a seção.

#### Scenario: Painel lista anexos com visualização

- **GIVEN** um caso com um anexo PDF processado
- **WHEN** o manager abre o detalhe no painel
- **THEN** a seção de anexos exibe nome, status e a visualização colapsável do arquivo

#### Scenario: Painel sem anexos omite a seção

- **GIVEN** um caso sem anexos
- **WHEN** o manager abre o detalhe no painel
- **THEN** nenhuma seção de anexos é renderizada
