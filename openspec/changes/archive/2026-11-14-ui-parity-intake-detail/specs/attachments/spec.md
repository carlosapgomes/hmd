# Delta: attachments

## MODIFIED Requirements

### Requirement: Upload e listagem de anexos pelo NIR

O sistema SHALL permitir ao NIR anexar arquivos (JPEG, PNG ou PDF) no momento da criação do caso e no reenvio corrigido, respeitando limites de contagem e tamanho por arquivo, com rejeição nomeada antes de qualquer gravação; anexos aceitos ficam listados no detalhe do caso para o criador com nome e status de processamento legível. O caso sem anexos SHALL seguir idêntico ao fluxo atual. Anexos exigem exatamente 1 relatório: com múltiplos PDFs, anexos geram erro nomeado NÃO-bloqueante (casos criados sem anexos); com 1 PDF, anexo inválido ABORTA o envio inteiro.

#### Scenario: Anexos criados junto com o caso

- **GIVEN** um upload com 1 PDF de relatório e dois anexos válidos (uma foto JPEG e um PDF)
- **WHEN** a criação é submetida
- **THEN** o caso é criado com seu documento e as rows de anexo com os arquivos gravados, ambos na mesma transação

#### Scenario: Anexo fora dos limites é rejeitado sem efeito

- **GIVEN** um upload de 1 relatório com um anexo de tipo não aceito ou acima do limite de tamanho/contagem
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
