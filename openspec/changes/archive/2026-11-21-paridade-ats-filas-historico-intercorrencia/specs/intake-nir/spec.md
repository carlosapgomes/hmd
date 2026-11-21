# Spec Delta

## ADDED Requirements

### Requirement: Busca de casos encerrados e detalhe histórico

O sistema SHALL oferecer ao papel ativo `nir` uma página de busca de casos encerrados com
busca server-side por nº de ocorrência OU nome do paciente sobre casos `CLEANED` e casos
com intercorrência ativa (qualquer status) — **de qualquer criador** (escopo histórico,
guarda própria fail-closed; o escopo por criador de «meus casos» permanece intacto) — com
no máximo 50 resultados por consulta, estado vazio sem critério e identificação de
elegibilidade para intercorrência pós-encerramento (spec `post-closure-issue`). A busca
SHALL usar a referência humana nos resultados (nº de ocorrência com sufixo curto de
desambiguação quando duplicado) e o uid completo NÃO aparece em texto visível. O detalhe
histórico SHALL ser read-only (trilha de eventos incluída, fechada por padrão), sem ações
de edição do caso, com formulário de abertura de intercorrência apenas para casos
elegíveis. Rotas de busca e detalhe histórico são regidas pelo guard de intranet do papel
`nir` (ADR-0002).

#### Scenario: Busca por nome encontra caso encerrado de outro criador

- **GIVEN** um caso `CLEANED` criado por outro usuário NIR, cujo nome do paciente contém o termo buscado
- **WHEN** o NIR busca pelo nome na página de casos encerrados
- **THEN** o caso é listado com referência humana, status legível e indicação de elegibilidade, sem expor o uid completo

#### Scenario: Busca por número de ocorrência

- **GIVEN** casos encerrados com nº de ocorrência distintos, incluindo um com intercorrência ativa
- **WHEN** o NIR busca pelo nº
- **THEN** apenas os casos correspondentes são listados, incluindo o caso com intercorrência ativa (critério de inclusão), este sem ação de nova abertura

#### Scenario: Casos em andamento não elegíveis não aparecem

- **GIVEN** casos em status anteriores ao encerramento e sem intercorrência ativa
- **WHEN** o NIR busca pelo nome ou nº deles
- **THEN** nenhum deles é listado — o universo da busca é encerrados + intercorrências ativas

#### Scenario: Sem critério mantém estado vazio

- **GIVEN** a página de busca aberta sem termo
- **WHEN** renderizada
- **THEN** nenhum resultado é exibido e um estado vazio orienta o uso da busca

#### Scenario: Detalhe histórico é read-only com trilha

- **GIVEN** um resultado elegível na busca
- **WHEN** o NIR abre o detalhe histórico
- **THEN** o caso é exibido em leitura com identificação, resultado, comunicações e a trilha de eventos fechada por padrão; nenhum formulário de edição do caso é oferecido e o formulário de intercorrência aparece apenas se o caso for elegível

#### Scenario: Papel não NIR recebe 403

- **GIVEN** usuário com papel ativo `scheduler`
- **WHEN** acessa a busca de casos encerrados
- **THEN** recebe HTTP 403

#### Scenario: Caso fora do escopo histórico retorna 404

- **GIVEN** um identificador de caso em andamento sem intercorrência ativa
- **WHEN** o NIR acessa o detalhe histórico diretamente pela URL
- **THEN** recebe HTTP 404, sem expor qualquer conteúdo do caso
