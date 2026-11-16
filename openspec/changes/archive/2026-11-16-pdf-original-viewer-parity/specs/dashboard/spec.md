# Delta: dashboard

## MODIFIED Requirements

### Requirement: Detalhe do caso no painel com trilha legível

O painel SHALL exibir o detalhe de um caso (manager/admin) com a identificação completa do paciente (nome, idade, sexo, raça/cor, unidade de origem, nº de ocorrência, data/hora de inserção e fase/status) e os procedimentos declarados, hospedando a ação de encerramento administrativo para casos não concluídos. O detalhe SHALL incluir a **trilha de eventos com rótulos legíveis** — mapa dos tipos de evento do sistema para português claro (molde ats-web `EVENT_LABELS`; tipos fora do mapa exibem o valor bruto), com data/hora e ator — em card **collapsible fechado por padrão** (Bootstrap). O detalhe SHALL incluir ainda os cards `Texto Extraído do PDF` e `Visualizar PDF Original` (spec `pdf-original-viewer`), servidos por rotas próprias do painel sob o mesmo guard `manager`/`admin`. Caso inexistente → 404 sem vazar informação.

#### Scenario: Detalhe com identificação completa

- **GIVEN** um caso identificado pelo cabeçalho SESAB
- **WHEN** o manager abre o detalhe pelo botão [Detalhes]
- **THEN** nome, idade, sexo, raça/cor, unidade de origem, nº de ocorrência, data/hora de inserção e fase aparecem, com os procedimentos declarados

#### Scenario: Trilha com rótulos legíveis collapsible

- **GIVEN** um caso com trilha de eventos em diversos tipos
- **WHEN** o detalhe é renderizado
- **THEN** a trilha aparece em card fechado por padrão; ao abrir, cada evento tem rótulo legível do mapa (fallback = valor bruto), data/hora e ator

#### Scenario: Encerramento administrativo a partir do detalhe

- **GIVEN** um caso ativo detalhado por manager/admin
- **WHEN** o detalhe é inspecionado
- **THEN** a ação de encerramento administrativo está disponível (fluxo existente preservado); caso `CLEANED` não oferece

#### Scenario: Caso inexistente não vaza

- **GIVEN** um identificador inexistente
- **WHEN** o detalhe é acessado
- **THEN** retorna 404 sem informação de outros casos

#### Scenario: Detalhe do painel exibe documentos e texto

- **GIVEN** um caso com texto extraído e documentos
- **WHEN** o manager abre o detalhe
- **THEN** os cards `Texto Extraído do PDF` e `Visualizar PDF Original` aparecem fechados por padrão, com serve e viewer sob rotas próprias do painel
