# Delta: intake-nir

## MODIFIED Requirements

### Requirement: Criação de caso com upload multi-PDF e declaração de tipos

O NIR (papel ativo `nir`) SHALL criar casos enviando de 1 a N arquivos PDF (um relatório por PDF) e declarando um único tipo de procedimento do catálogo, aplicado a todo o lote (lote homogêneo). A validação do lote (tipo ausente/fora do catálogo, lote vazio, limites de contagem e tamanho) SHALL abortar o envio inteiro sem criar nada. Arquivo inválido dentro de um lote válido SHALL virar erro nomeado sem bloquear os demais: cada PDF válido cria seu próprio caso em `NEW` (transação independente), com documentos ordenados e o tipo declarado; falhas de persistência de um caso SHALL virar erro por arquivo, preservando os já criados. Anexos (JPEG, PNG ou PDF) SHALL ser aceitos somente com exatamente 1 relatório: com múltiplos PDFs, anexos geram erro nomeado NÃO-bloqueante (casos criados sem anexos); com 1 PDF, anexo inválido ABORTA o envio inteiro. Tipos fora do catálogo rejeitados pela validação existente.

#### Scenario: Criação com múltiplos PDFs e tipos declarados

- **GIVEN** um usuário com papel ativo `nir`
- **WHEN** envia 2 PDFs válidos e declara um tipo do catálogo para o lote
- **THEN** dois casos nascem em `NEW` (um por PDF), cada um com seu documento e o tipo declarado, com evento de declaração na trilha

#### Scenario: Arquivo não-PDF rejeita a criação inteira

- **GIVEN** um usuário com papel ativo `nir`
- **WHEN** envia 1 PDF e 1 imagem (não-PDF) com tipo válido
- **THEN** o PDF válido cria seu caso e o erro identifica o arquivo inválido, sem bloquear o caso criado

#### Scenario: Limite de quantidade excede

- **GIVEN** a configuração de limite de documentos por caso
- **WHEN** o NIR envia mais arquivos que o limite
- **THEN** a criação é rejeitada com erro claro antes de qualquer persistência

#### Scenario: Anexos com múltiplos PDFs geram erro não-bloqueante

- **GIVEN** 2 PDFs válidos e anexos
- **WHEN** o NIR submete o envio
- **THEN** os dois casos são criados sem anexos e o erro nomeado informa que anexos exigem exatamente 1 relatório

#### Scenario: Anexo inválido com 1 PDF aborta o envio

- **GIVEN** 1 PDF válido e 1 anexo acima do limite, com tipo válido
- **WHEN** o NIR submete o envio
- **THEN** nenhum caso é criado e o erro nomeia o anexo inválido
