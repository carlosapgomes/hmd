# attachments Specification (delta)

## Purpose

Processar anexos de evidência dos casos com OCR híbrido (local para PDFs com texto, externo por visão para fotos/scans), anonimizar o texto extraído antes de qualquer LLM, verificar a identidade do paciente e exibir o resultado como alerta consultivo na decisão médica — com auditoria do envio externo e limpeza no fechamento.

## ADDED Requirements

### Requirement: Upload e listagem de anexos pelo NIR

O sistema SHALL permitir ao NIR anexar arquivos (JPEG, PNG ou PDF) no momento da criação do caso e no reenvio corrigido, respeitando limites de contagem e tamanho por arquivo, com rejeição nomeada antes de qualquer gravação; anexos aceitos ficam listados no detalhe do caso para o criador com nome e status de processamento legível. O caso sem anexos SHALL seguir idêntico ao fluxo atual.

#### Scenario: Anexos criados junto com o caso

- **GIVEN** um upload de caso com dois PDFs de relatório e dois anexos (uma foto JPEG e um PDF)
- **WHEN** a criação é submetida
- **THEN** o caso é criado com seus documentos e as rows de anexo com os arquivos gravados, ambos na mesma transação

#### Scenario: Anexo fora dos limites é rejeitado sem efeito

- **GIVEN** um upload com um anexo de tipo não aceito ou acima do limite de tamanho/contagem
- **WHEN** a criação é submetida
- **THEN** a validação falha com erro nomeado e nada é gravado (nem caso, nem documentos)

#### Scenario: NIR vê seus anexos com status

- **GIVEN** um caso do NIR com um anexo já processado e outro pendente
- **WHEN** ele abre o detalhe do caso
- **THEN** ambos os anexos são listados com nome e status legível de processamento

### Requirement: Extração híbrida com auditoria de OCR externo

O sistema SHALL extrair o texto de cada anexo de forma assíncrona após a anonimização do relatório principal: PDFs com camada de texto extraem localmente; fotos, scans e PDFs-imagem usam OCR externo por modelo de visão, com evento de auditoria registrado antes do envio externo. Falha de extração SHALL marcar o anexo como falho com motivo, sem bloquear o andamento do caso.

#### Scenario: PDF com camada de texto extrai localmente

- **GIVEN** um anexo PDF com camada de texto pendente
- **WHEN** o processamento do caso roda
- **THEN** o texto é extraído localmente, o anexo fica processado com método local e nenhum evento de OCR externo é registrado

#### Scenario: Foto de laudo usa OCR externo com auditoria

- **GIVEN** um anexo JPEG pendente
- **WHEN** o processamento roda
- **THEN** a imagem é transcrita pelo modelo de visão e um evento de auditoria do envio externo fica registrado na trilha do caso antes do envio

#### Scenario: PDF-imagem usa OCR externo

- **GIVEN** um anexo PDF sem camada de texto (imagem digitalizada)
- **WHEN** o processamento roda
- **THEN** as páginas são transcritas pelo modelo de visão e o evento de auditoria é registrado

#### Scenario: Falha de OCR externo não bloqueia o caso

- **GIVEN** um anexo cujo OCR externo falha
- **WHEN** o processamento roda
- **THEN** o anexo fica com status de falha e motivo registrado em evento, e o caso segue seu fluxo normalmente

### Requirement: Verificação de paciente anonimizada

O texto extraído do anexo SHALL ser anonimizado antes de qualquer chamada LLM, no mesmo espaço determinístico de tokens do caso; a verificação por LLM SHALL receber apenas tokens (texto anonimizado do anexo e o pseudônimo do paciente do caso) e produzir `match`, `mismatch` ou `unknown` com resumo e evidência, persistidos na row do anexo e em evento. Falha da verificação SHALL marcar o anexo como falho sem efeito sobre o caso.

#### Scenario: Verificação retorna resultado persistido

- **GIVEN** um anexo com texto extraído do mesmo paciente do caso
- **WHEN** a verificação roda
- **THEN** o resultado é `match` com resumo e evidência, persistidos na row e em evento de anexo processado

#### Scenario: LLM de verificação só vê tokens

- **GIVEN** um anexo com dados reais de paciente no texto extraído
- **WHEN** a chamada de verificação é montada
- **THEN** o conteúdo enviado contém exclusivamente tokens — nenhum nome, documento ou data real presente nos mapas do anexo/caso

#### Scenario: Falha de verificação é registrada

- **GIVEN** um anexo cuja chamada LLM de verificação falha
- **WHEN** o processamento roda
- **THEN** o anexo fica com status de falha e motivo em evento, e o caso segue normalmente

### Requirement: Anexos na decisão médica

A tela de decisão médica SHALL exibir, quando houver anexos, um card por anexo com status, método, resultado da verificação e resumo/evidência re-identificados apenas na renderização; divergência (`mismatch`) SHALL aparecer como alerta consultivo sem nunca descartar ou bloquear o caso. Casos sem anexos permanecem visualmente idênticos.

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
