# Spec Delta

## MODIFIED Requirements

### Requirement: Card de prior-case com motivo real

O detalhe do caso SHALL exibir, por procedimento declarado com caso prévio encontrado pelas regras de identidade e janela do pipeline, um card com data, desfecho, origem e motivo real da decisão prévia. O badge SHALL usar a referência desambiguada `nº · <8 chars>` ou `uid <8 chars>`, nunca uid completo. O presenter SHALL ser somente-leitura, respeitar confirmação NIR quando necessária e distinguir contexto confirmado após a sumarização de contexto já consumido pelo LLM2.

#### Scenario: Card de negativa prévia por número de ocorrência
- **GIVEN** caso prévio negado há 3 dias com nome, procedimento e número de ocorrência iguais
- **WHEN** médico abre o detalhe
- **THEN** card exibe data, desfecho negado e motivo real do médico prévio

#### Scenario: PDF original restrito a papel autorizado
- **GIVEN** um caso com documento PDF
- **WHEN** NIR tenta acessar diretamente a URL do PDF médico
- **THEN** recebe HTTP 403 sem conteúdo do arquivo

#### Scenario: Card de prior-case por fallback nome+nascimento
- **GIVEN** caso prévio negado há 10 dias com regulação diferente, nome e CNS válidos iguais, sem nascimento
- **WHEN** médico abre o detalhe
- **THEN** card exibe prévio com desfecho e motivo real, marcando origem CNS

#### Scenario: Badge do prior-case usa referência desambiguada
- **GIVEN** caso prévio com nº N igual ao do corrente
- **WHEN** card é renderizado
- **THEN** badge exibe `N · <8 chars do prévio>`, sem uid completo visível

#### Scenario: Contexto confirmado depois da sumarização
- **WHEN** NIR confirma negativa anterior após LLM2 já ter sumarizado o novo
- **THEN** card médico mostra origem `nir_confirmed` e inclusão posterior, sem alegar consumo pelo LLM2, disparar reanálise ou alterar decisão já registrada

## ADDED Requirements

### Requirement: Espera preservada na substituição médica

Cards pendentes SHALL exibir a espera acumulada herdada nas substituições de reapresentação, sem modificar criação nem dias em tela extraídos. FIFO SHALL usar esse marco como desempate, preservando prioridade principal por dias em tela. Abas decididas e guards existentes permanecem inalterados.

#### Scenario: Reapresentações sucessivas
- **WHEN** terceiro envio substitui segundo, que já substituiu primeiro ainda não decidido
- **THEN** card novo mostra espera desde o primeiro marco e criação real continua sendo a do terceiro

#### Scenario: Dias em tela continuam oficiais
- **WHEN** novo relatório traz dias em tela diferentes do original
- **THEN** ordenação principal e badge usam o novo valor extraído, sem fabricar dias para manter posição absoluta

#### Scenario: Sem reapresentação
- **WHEN** caso sem vínculo aplicado é listado
- **THEN** espera usa criação como antes e paginação, subtipo, polling e reserva permanecem vigentes
