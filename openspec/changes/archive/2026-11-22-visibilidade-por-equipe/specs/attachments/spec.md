# attachments Specification (delta)

## Purpose

Upload/listagem/visualização de anexos de evidência — escopo de equipe `nir`: listagem e serve de anexos de casos de qualquer criador (decisão do dono 2026-11-22).

## MODIFIED Requirements

### Requirement: Upload e listagem de anexos pelo NIR

O sistema SHALL permitir ao NIR anexar arquivos (JPEG, PNG ou PDF) no momento da criação do caso e no reenvio corrigido, respeitando limites de contagem e tamanho por arquivo, com rejeição nomeada antes de qualquer gravação; anexos são aceitos SOMENTE quando o envio contém exatamente 1 PDF de relatório (com múltiplos PDFs a vinculação seguro entre relatórios e anexos é impossível) — nesse caso os casos do lote são criados sem anexos e o erro informa a restrição; com exatamente 1 PDF, anexo inválido aborta o envio inteiro. Anexos aceitos ficam listados no detalhe do caso para o NIR (equipe — qualquer criador, decisão do dono 2026-11-22) com nome, status de processamento legível e **visualização do arquivo** (link de abertura + embed/imagem colapsável, padrão `pdf-original-viewer`), servida por rota própria do NIR sob o guard de papel ativo `nir` (equipe — qualquer criador). O caso sem anexos SHALL seguir idêntico ao fluxo atual.

#### Scenario: Anexos criados junto com o caso

- **GIVEN** um upload de caso com exatamente 1 PDF de relatório e dois anexos (uma foto JPEG e um PDF)
- **WHEN** a criação é submetida
- **THEN** o caso é criado com seu documento e as rows de anexo com os arquivos gravados, ambos na mesma transação

#### Scenario: Anexo fora dos limites é rejeitado sem efeito

- **GIVEN** um upload com exatamente 1 PDF de relatório e um anexo de tipo não aceito ou acima do limite de tamanho/contagem
- **WHEN** a criação é submetida
- **THEN** a validação falha com erro nomeado e nada é gravado (nem caso, nem documentos)

#### Scenario: NIR vê seus anexos com status

- **GIVEN** um caso com um anexo já processado e outro pendente
- **WHEN** o NIR (criador ou não) abre o detalhe do caso
- **THEN** ambos os anexos são listados com nome e status legível de processamento

#### Scenario: Anexos com múltiplos relatórios são recusados sem bloquear

- **GIVEN** um upload com 2 PDFs de relatório válidos e dois anexos
- **WHEN** a criação é submetida
- **THEN** os dois casos são criados sem anexos e o erro nomeado informa que anexos exigem exatamente 1 relatório

#### Scenario: NIR visualiza o arquivo do anexo

- **GIVEN** um caso com um anexo PDF e um JPEG
- **WHEN** o NIR (criador ou não) abre o detalhe do caso
- **THEN** cada anexo tem link de abertura do arquivo e visualização colapsável (embed lazy para PDF, imagem lazy para JPEG); anexo de caso de outro criador serve normalmente à equipe `nir`, e usuário sem papel ativo `nir` é negado pelo guard de papel
