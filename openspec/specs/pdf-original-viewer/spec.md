# pdf-original-viewer Specification

## Purpose

Visualização do relatório original e dos anexos de evidência nos detalhes de caso dos quatro papéis (NIR, médico, agendador e painel): cards colapsáveis padronizados («Texto Extraído do PDF» e «Visualizar PDF Original»), embed lazy por documento no desktop com fallback sem-JavaScript, viewer mobile dedicado com PDF.js e botão Voltar, guards por papel idênticos aos dos detalhes (papel errado → 403; alcance, documento/anexo inexistente ou sem arquivo → 404) e mídia de verificação isolada por run.

## Requirements

### Requirement: Cards de texto extraído e PDF original

Os detalhes de caso dos papéis `nir`, `doctor`, `scheduler` e `manager`/`admin` SHALL exibir dois cards colapsáveis fechados por padrão: `Texto Extraído do PDF` (texto do relatório em `<pre>`) e `Visualizar PDF Original` (um sub-collapse por documento do relatório, na ordem declarada). Sem texto extraído, o card de texto SHALL ser omitido — exceto no detalhe médico, que exibe placeholder de indisponibilidade.

#### Scenario: NIR vê os dois cards colapsáveis

- **GIVEN** um caso do próprio NIR com texto extraído e documentos
- **WHEN** o NIR abre o detalhe
- **THEN** os cards `Texto Extraído do PDF` e `Visualizar PDF Original` aparecem fechados por padrão, o primeiro com o texto em `<pre>` e o segundo com um sub-collapse por documento

#### Scenario: Médico sem texto vê placeholder

- **GIVEN** um caso acessível ao médico ainda sem texto extraído
- **WHEN** o médico abre o detalhe ou a página de decisão
- **THEN** o card de texto exibe o placeholder de indisponibilidade em vez de ser omitido

#### Scenario: Painel exibe os dois cards

- **GIVEN** um caso com texto extraído e documentos
- **WHEN** o manager/admin abre o detalhe no painel
- **THEN** os dois cards aparecem com o mesmo comportamento dos papéis operacionais


### Requirement: Embed lazy do PDF no desktop

Em viewport desktop (breakpoint `md` ou maior) cada documento SHALL ser renderizado num `<embed type="application/pdf">` dentro do seu sub-collapse, com a URL de serve em `data-src` e o atributo `src` definido apenas na primeira expansão (carregamento lazy). Cada documento SHALL ter link "Abrir em nova aba" para a rota de serve, e a página SHALL incluir fallback `<noscript>` com links diretos de abertura por documento, fora dos collapses.

#### Scenario: Collapse fechado não busca o PDF

- **GIVEN** o detalhe de um caso com documentos
- **WHEN** a página é renderizada com os collapses fechados
- **THEN** o HTML traz os embeds sem `src` e com `data-src` apontando a rota de serve, e nenhuma requisição ao PDF é feita

#### Scenario: Primeira expansão carrega o PDF

- **GIVEN** o detalhe com o sub-collapse de um documento fechado
- **WHEN** o usuário expande o sub-collapse
- **THEN** o `src` do embed é definido a partir do `data-src` e o PDF é carregado uma única vez

#### Scenario: Resize para desktop carrega o sub-collapse aberto

- **GIVEN** um sub-collapse de documento aberto em viewport mobile (embed ainda sem `src`)
- **WHEN** o viewport passa ao breakpoint desktop
- **THEN** o PDF do documento aberto é carregado sem exigir nova expansão

#### Scenario: Abertura direta funciona sem JavaScript

- **GIVEN** o detalhe renderizado com JavaScript desabilitado
- **WHEN** o usuário abre a página
- **THEN** links diretos de abertura por documento estão visíveis fora dos collapses (bloco `<noscript>`) e abrem o PDF na rota de serve do papel


### Requirement: Viewer mobile dedicado com Voltar

Em viewport mobile (abaixo de `md`) o card SHALL exibir link para o viewer dedicado do papel; o embed desktop SHALL permanecer oculto e o mobile nunca SHALL iniciar seu carregamento (sem atribuição de `src`); `src` já atribuído em desktop pode ser retido no resize reverso. O viewer SHALL renderizar o PDF com PDF.js vendored (indicador de loading; erro com link fallback para o PDF direto) e botão `← Voltar` no topo (sticky) e no rodapé, voltando ao `?next=` quando for URL segura do mesmo host ou ao detalhe do caso.

#### Scenario: Mobile mostra link e nunca busca o embed

- **GIVEN** o detalhe de um caso com documentos em viewport mobile
- **WHEN** a página é renderizada e navegada (incluindo expansões disponíveis)
- **THEN** cada documento exibe link para o viewer dedicado e nenhuma requisição aos PDFs dos embeds é feita

#### Scenario: Resize para mobile mantém o já carregado

- **GIVEN** um embed com `src` atribuído em viewport desktop
- **WHEN** o viewport volta ao breakpoint mobile
- **THEN** nenhum novo carregamento é iniciado e o conteúdo já carregado pode permanecer

#### Scenario: Viewer renderiza com Voltar no topo e no rodapé

- **GIVEN** o viewer dedicado de um documento
- **WHEN** a página carrega
- **THEN** o PDF é renderizado via PDF.js e há botão `← Voltar` na nav sticky do topo e no rodapé, ambos para o detalhe do caso

#### Scenario: Next inseguro cai no fallback

- **GIVEN** o viewer acessado com `?next=https://evil.example/`
- **WHEN** a página é renderizada
- **THEN** os botões Voltar apontam o detalhe do caso, nunca a URL externa


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
