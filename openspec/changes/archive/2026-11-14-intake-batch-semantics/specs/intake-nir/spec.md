# intake-nir Specification (delta) — rev. 2

## REMOVED Requirements

### Requirement: Criação de caso com upload multi-PDF e declaração de tipos

Removido porque a semântica mudou por decisão do dono (2026-09-14, molde
ats-web): N PDFs NÃO são partes de um relatório — cada PDF é um relatório de
um paciente e vira um caso independente; o tipo é único por envio/lote
(múltiplos tipos não são permitidos). O requisito substituto é "Envio em
lote: um PDF por caso, tipo único".

## ADDED Requirements

### Requirement: Envio em lote: um PDF por caso, tipo único

O NIR (papel ativo `nir`) SHALL enviar de 1 a N arquivos PDF num único envio, onde cada PDF é o relatório de um paciente e vira um caso independente. Cada caso SHALL nascer atomicamente (caso em `NEW`, exatamente 1 documento, declaração do único tipo do lote e eventos juntos ou nada). O tipo de procedimento SHALL ser único por envio/lote (validado contra o catálogo; múltiplos tipos não são permitidos). O lote SHALL respeitar limites configurados de contagem de arquivos, tamanho por arquivo e tamanho total, rejeitados com erro claro antes de qualquer persistência. Falhas por arquivo (não-PDF, tamanho) SHALL rejeitar apenas aquele arquivo: os válidos viram casos e o resultado lista os casos criados e os erros por arquivo; exceção de persistência num caso do lote SHALL resultar em erro por arquivo, preservando os casos já criados. Anexos seguem a regra da spec `attachments` (somente com exatamente 1 PDF; no lote multi-PDF com anexos enviados, os casos são criados sem anexos e o erro informa a restrição). O envio com exatamente 1 PDF e anexo inválido SHALL abortar todo o envio (nada criado).

#### Scenario: Lote de dois PDFs cria dois casos independentes

- **GIVEN** um usuário com papel ativo `nir`
- **WHEN** envia 2 PDFs válidos declarando um único tipo do catálogo
- **THEN** dois casos independentes nascem em `NEW`, cada um com exatamente 1 documento e o tipo declarado, com evento de declaração por caso na trilha

#### Scenario: Arquivo inválido no lote rejeita apenas ele

- **GIVEN** um lote com 2 PDFs válidos e 1 imagem (não-PDF)
- **WHEN** o envio é submetido
- **THEN** os 2 casos dos PDFs válidos são criados e o resultado lista o erro nomeado do arquivo inválido, sem interromper os demais

#### Scenario: Tipo único inválido rejeita o lote inteiro

- **GIVEN** um envio com tipo fora do catálogo (ou mais de um tipo)
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

#### Scenario: Cada caso do lote é atomicamente criado

- **GIVEN** um lote em que o 2º arquivo falha na persistência após o 1º caso já criado
- **WHEN** o envio é processado
- **THEN** o 1º caso permanece criado e íntegro e o resultado lista o erro do 2º arquivo

## MODIFIED Requirements

### Requirement: Revisão NIR do gate

O NIR SHALL conseguir revisar casos retidos pelo gate na tela de detalhe, com duas ações: **liberar** (bypass — o caso avança para `ANONYMIZING` com evento de bypass registrado) e **reenviar documento** (substitui o PDF do caso por exatamente 1 novo arquivo — erro nomeado caso contrário, sem efeito — e reprocessa do início da extração, zerando a flag e o texto anterior). Ambas as ações ficam disponíveis apenas enquanto o caso estiver retido.

#### Scenario: Liberar avança com evento de bypass

- **GIVEN** um caso retido em `PDF_EXTRACTING` com `manual_review_required=True`
- **WHEN** o NIR libera o caso
- **THEN** o caso avança para `ANONYMIZING` e a trilha registra o evento de bypass com o NIR como ator

#### Scenario: Reenviar documentos reprocessa do zero

- **GIVEN** um caso retido com documento inválido
- **WHEN** o NIR reenvia exatamente 1 PDF válido
- **THEN** o documento antigo é substituído, a flag de revisão é zerada, o texto anterior é descartado e o caso é reprocessado

#### Scenario: Reenvio do gate exige exatamente um PDF

- **GIVEN** um caso retido
- **WHEN** o NIR reenvia 0 ou mais de 1 arquivo
- **THEN** nada é alterado e o erro nomeado informa que o reenvio aceita exatamente 1 PDF
