# Spec Delta

## MODIFIED Requirements

### Requirement: Guards de serve e viewer por papel

Serve e viewer SHALL aplicar guards equivalentes: papel errado recebe 403; NIR acessa casos de qualquer criador; médico fora da matriz de subtipo recebe 403; agendador fora do gate de decisão de agendamento + estado pós-decisão recebe 404, inclusive negativa médica sem decisão de agendamento. Documento/posição inexistente, arquivo ausente ou viewer de não-PDF recebem 404 sem distinguir motivos. EXCEÇÃO estreita: rotas de documentos de reapresentações aplicadas ao original acessível ao agendador SHALL permitir relatório/anexos recebidos enquanto aguarda agendamento e no Histórico do Agendador em CLEANED retido, sem abrir o PDF original fora de seu gate anterior, seguindo relação exata entre original, vínculo e arquivo.

#### Scenario: NIR não serve caso alheio
- **GIVEN** caso de qualquer criador
- **WHEN** papel não NIR pede serve/viewer NIR
- **THEN** recebe 403 sem conteúdo

#### Scenario: Médico fora da matriz recebe 403
- **WHEN** médico de subtipo incompatível pede serve/viewer
- **THEN** recebe 403 em ambas as rotas

#### Scenario: Agendador fora do gate recebe 404
- **GIVEN** caso reaberto fora do gate ou negativa médica sem decisão de agendamento
- **WHEN** agendador pede serve/viewer do PDF ORIGINAL
- **THEN** recebe 404, independentemente de haver reapresentações

#### Scenario: Painel exige manager ou admin
- **WHEN** outro papel pede serve/viewer do painel
- **THEN** recebe 403

#### Scenario: NIR não-criador serve caso da equipe
- **WHEN** NIR diferente do criador pede serve/viewer
- **THEN** documento é servido normalmente

#### Scenario: Agendador serve PDF de caso processado por outro agendador
- **GIVEN** decisão de agendamento por outro operador e estado pós-decisão
- **WHEN** agendador pede serve/viewer original
- **THEN** documento é servido normalmente

#### Scenario: Documento recebido acessível antes da decisão de agendamento
- **GIVEN** original aguardando agendamento com reapresentação aplicada
- **WHEN** agendador pede serve/viewer do relatório recebido pela rota vinculada
- **THEN** documento recebido é servido e o PDF original continua inacessível fora do gate antigo

#### Scenario: Histórico CLEANED retido autoriza apenas recebidos
- **WHEN** agendador consulta serve/viewer vinculado a canônico CLEANED retido com vínculo aplicado exato
- **THEN** PDF/anexo recebido existente é servido, mesmo com feature desligada; PDF original em CLEANED continua retornando 404

#### Scenario: Histórico minimizado não serve arquivos ausentes
- **WHEN** canônico CLEANED e incoming foram minimizados ou purgados
- **THEN** serve/viewer vinculados retornam 404 sem expor caminho de storage

#### Scenario: IDs trocados não ampliam autorização
- **WHEN** agendador troca vínculo, incoming, original ou arquivo por IDs sem a relação aplicada exata
- **THEN** serve e viewer retornam 404, mesmo que outro caso seja acessível

#### Scenario: Viewer preserva segurança e apresentação
- **WHEN** documento vinculado é aberto em mobile com next externo ou em desktop com collapse fechado
- **THEN** next inseguro cai no detalhe autorizado e embed fechado não carrega antecipadamente, preservando PDF.js e fallback sem JS
