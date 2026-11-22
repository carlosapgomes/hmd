# Spec Delta

## RENAMED Requirements

- FROM: `### Requirement: Catálogo de 13 tipos de procedimento`
- TO: `### Requirement: Catálogo de tipos de procedimento do HMD`

## MODIFIED Requirements

### Requirement: Catálogo de tipos de procedimento do HMD

O sistema SHALL definir, em código (registro de perfis), exatamente os 14 tipos de procedimento do HMD, cada um com nome canônico, rótulo de exibição, seção de critérios clínicos (S1–S8), subtipo de doctor (angio/neuro/cardio/radio) e indicação de suporte anestésico. A neurointervenção (embolização de aneurisma ou malformação vascular intracraniana) SHALL usar a seção de critérios S2, o subtipo doctor `neuro` e suporte anestésico. Existir no catálogo equivale a estar habilitado (sem flag separada). A tabela de thresholds por seção SHALL estar disponível como dados consultáveis. Novos tipos são adicionados exclusivamente por mudança de código (change explícito), sem migração de dados.

#### Scenario: Catálogo completo e consultável

- **GIVEN** o catálogo de perfis carregado
- **WHEN** os 14 tipos são consultados
- **THEN** cada um retorna label, seção de critérios, subtipo doctor e flag de suporte anestésico coerentes com o documento de parâmetros clínicos (flebografia usa a seção 1; angio_carotidas usa a seção 2; neurointervenção usa a seção 2)

#### Scenario: Tipo desconhecido é rejeitado

- **GIVEN** um identificador de tipo que não consta no catálogo
- **WHEN** é usado para criar um procedimento de caso
- **THEN** a operação é rejeitada com erro explícito

#### Scenario: Seed de verificação idempotente

- **GIVEN** o banco do HMD
- **WHEN** o comando de seed do catálogo é executado duas vezes
- **THEN** a segunda execução não duplica nada e reporta sucesso

#### Scenario: Neurointervenção roteada ao subtipo neuro

- **GIVEN** um caso com o tipo `neurointerv` declarado por NIR
- **WHEN** a fila médica é computada para um médico com subtipo `neuro`
- **THEN** o caso é visível a esse médico; um médico restrito a `cardio` não o vê
