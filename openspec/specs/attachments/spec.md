# attachments Specification

## Purpose

Processar anexos de evidência dos casos com OCR híbrido (local para PDFs com texto, externo por visão para fotos/scans), anonimizar o texto extraído antes de qualquer LLM, verificar a identidade do paciente e exibir o resultado como alerta consultivo na decisão médica — com auditoria do envio externo e limpeza no fechamento.

## Requirements

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
