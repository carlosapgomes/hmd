# case-closure Specification (delta)

## MODIFIED Requirements

### Requirement: Reenvio corrigido cria novo caso vinculado

O sistema SHALL permitir ao NIR reenviar, de forma corrigida, um caso seu encerrado: cria um novo caso que percorre o pipeline completo desde o início, vinculado ao original com motivo obrigatório e exatamente 1 PDF de relatório + um único tipo declarado explicitamente (nunca herdado; múltiplos tipos e múltiplos arquivos são rejeitados com erro nomeado, sem criar o caso). O caso original SHALL permanecer inalterado exceto pelo evento de supersedição e pela listagem dos reenvios que o corrigem.

#### Scenario: Reenvio cria novo caso vinculado com motivo

- **GIVEN** um caso encerrado criado pelo NIR autenticado
- **WHEN** ele reenvia de forma corrigida com exatamente 1 PDF novo, tipo declarado e motivo
- **THEN** um novo caso é criado em processamento inicial vinculado ao original, e ambos registram os eventos de correção e supersedição

#### Scenario: Tipos do reenvio não são herdados

- **GIVEN** um caso encerrado cujo tipo original é um subtipo qualquer
- **WHEN** o NIR reenvia declarando um tipo diferente do original
- **THEN** o novo caso registra exatamente o tipo declarado no reenvio

#### Scenario: Original permanece encerrado e íntegro

- **GIVEN** um caso encerrado que sofreu um reenvio corrigido
- **WHEN** o original é consultado
- **THEN** permanece `CLEANED` com seus dados intactos, listando o reenvio que o corrige

#### Scenario: Reenvio exige caso encerrado próprio

- **GIVEN** um caso não encerrado e um caso encerrado criado por outro NIR
- **WHEN** o NIR autenticado tenta o reenvio corrigido de qualquer um deles
- **THEN** ambos são recusados com erro apropriado e nenhum caso é criado

#### Scenario: Reenvio com múltiplos arquivos ou tipos é rejeitado

- **GIVEN** um caso encerrado criado pelo NIR autenticado
- **WHEN** ele reenvia de forma corrigida com mais de 1 PDF ou mais de um tipo declarado
- **THEN** nenhum caso é criado e o erro nomeado informa as restrições
