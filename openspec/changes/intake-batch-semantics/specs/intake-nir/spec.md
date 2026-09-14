# intake-nir Specification (delta)

## REMOVED Requirements

### Requirement: Criação de caso com upload multi-PDF e declaração de tipos

Removido porque a semântica mudou por decisão do dono (2026-09-14, molde
ats-web): N PDFs NÃO são partes de um relatório — cada PDF é um relatório de
um paciente e vira um caso independente; o tipo é único por envio/lote
(múltiplos tipos não são permitidos). O requisito substituto é "Envio em
lote: um PDF por caso, tipo único".

## ADDED Requirements

### Requirement: Envio em lote: um PDF por caso, tipo único

O NIR (papel ativo `nir`) SHALL enviar de 1 a N arquivos PDF num único envio, onde cada PDF é o relatório de um paciente e vira um caso independente em `NEW` com exatamente 1 documento e o único tipo de procedimento declarado para o lote (validado contra o catálogo). O lote SHALL respeitar limites configurados de contagem de arquivos, tamanho por arquivo e tamanho total, rejeitados com erro claro antes de qualquer persistência. Falhas por arquivo (não-PDF, tamanho) SHALL rejeitar apenas aquele arquivo: os válidos viram casos e o resultado lista os casos criados e os erros por arquivo. Anexos seguem a regra da spec `attachments` (somente com exatamente 1 PDF; no lote multi-PDF com anexos enviados, os casos são criados sem anexos e o erro informa a restrição). O envio com exatamente 1 PDF e anexo inválido SHALL abortar todo o envio (nada criado).

#### Scenario: Lote de dois PDFs cria dois casos independentes

- **GIVEN** um usuário com papel ativo `nir`
- **WHEN** envia 2 PDFs válidos declarando um único tipo do catálogo
- **THEN** dois casos independentes nascem em `NEW`, cada um com exatamente 1 documento e o tipo declarado, com evento de declaração por caso na trilha

#### Scenario: Arquivo inválido no lote rejeita apenas ele

- **GIVEN** um lote com 2 PDFs válidos e 1 imagem (não-PDF)
- **WHEN** o envio é submetido
- **THEN** os 2 casos dos PDFs válidos são criados e o resultado lista o erro nomeado do arquivo inválido, sem interromper os demais

#### Scenario: Tipo único inválido rejeita o lote inteiro

- **GIVEN** um envio com tipo fora do catálogo (ou múltiplos tipos declarados)
- **WHEN** o envio é submetido
- **THEN** nenhum caso é criado e o erro informa a restrição de tipo único

#### Scenario: Limites do lote excedidos rejeitam tudo

- **GIVEN** a configuração de limites de contagem/tamanho do lote
- **WHEN** o envio excede arquivos por lote ou tamanho total
- **THEN** nada é criado e o erro informa o limite violado antes de qualquer persistência

#### Scenario: Enviar um único PDF com anexo inválido aborta tudo

- **GIVEN** um envio com exatamente 1 PDF válido e 1 anexo fora dos limites
- **WHEN** o envio é submetido
- **THEN** nenhum caso é criado e o erro nomeado do anexo é retornado

#### Scenario: Lote multi-PDF com anexos cria casos sem anexos

- **GIVEN** um envio com 2 PDFs válidos e anexos
- **WHEN** o envio é submetido
- **THEN** os 2 casos são criados sem anexos e o resultado informa que anexos só são permitidos com exatamente 1 relatório
