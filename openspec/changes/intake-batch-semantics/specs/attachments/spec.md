# attachments Specification (delta)

## MODIFIED Requirements

### Requirement: Upload e listagem de anexos pelo NIR

O sistema SHALL permitir ao NIR anexar arquivos (JPEG, PNG ou PDF) no momento da criação do caso e no reenvio corrigido, respeitando limites de contagem e tamanho por arquivo, com rejeição nomeada antes de qualquer gravação; anexos são aceitos SOMENTE quando o envio contém exatamente 1 PDF de relatório (com múltiplos PDFs a vinculação seguro entre relatórios e anexos é impossível) — nesse caso os casos do lote são criados sem anexos e o erro informa a restrição; com exatamente 1 PDF, anexo inválido aborta o envio inteiro. Anexos aceitos ficam listados no detalhe do caso para o criador com nome e status de processamento legível. O caso sem anexos SHALL seguir idêntico ao fluxo atual.

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

#### Scenario: Anexos com múltiplos PDFs não são vinculados

- **GIVEN** um envio com 2 PDFs de relatório e anexos
- **WHEN** o envio é submetido
- **THEN** os casos dos relatórios são criados sem anexos e o resultado informa que anexos só são permitidos com exatamente 1 relatório
