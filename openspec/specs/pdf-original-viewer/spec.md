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

As rotas de serve e viewer SHALL aplicar os guards já vigentes de cada papel: papel ativo fora do permitido SHALL ser negado com 403 (guard de papel); com o papel correto, violações de alcance respondem fail-closed conforme o guard vigente de cada papel — NIR: guard de papel `nir` (equipe — caso de qualquer criador, decisão do dono 2026-11-22); médico: fora da matriz de subtipo → 403 (guard vigente de subtipo do serve, decisão do dono 2026-11-15 — paridade serve×viewer e com o ats-web); agendador: fora do gate → 404 (gate por **decisão de agendamento + status pós-decisão**, não por decisor — decisão do dono 2026-11-22; negativa médica direta, sem decisão de agendamento, fica fora do gate) — e documento/posição inexistente ou viewer sem PDF SHALL responder 404 sem distinguir os motivos.

#### Scenario: NIR não serve caso alheio

- **GIVEN** um caso criado por qualquer NIR
- **WHEN** um usuário com papel ativo não NIR requisita o serve ou o viewer do documento
- **THEN** o acesso é negado pelo guard de papel (403), sem vazar conteúdo

#### Scenario: Médico fora da matriz recebe 403

- **GIVEN** um caso cujo conjunto declarado não inclui o subtipo do médico
- **WHEN** ele requisita o serve ou o viewer
- **THEN** recebe 403 (guard vigente de subtipo, idêntico ao serve aceito — decisão do dono)

#### Scenario: Agendador fora do gate recebe 404

- **GIVEN** um caso fora dos estados pós-decisão de agendamento (ex.: reaberto por intercorrência) OU um caso de negativa médica direta em `FINAL_REPLY_POSTED`/`AWAITING_NIR_ACK` sem decisão de agendamento registrada
- **WHEN** qualquer agendador requisita o serve ou o viewer
- **THEN** recebe 404

#### Scenario: Painel exige manager ou admin

- **GIVEN** um usuário com papel ativo fora de `manager`/`admin`
- **WHEN** requisita o serve ou o viewer do painel
- **THEN** o acesso é negado (403 do guard de papel)

#### Scenario: NIR não-criador serve caso da equipe

- **GIVEN** um caso criado por outro usuário NIR
- **WHEN** um NIR diferente requisita o serve ou o viewer do documento
- **THEN** o documento é servido normalmente (equipe `nir`)

#### Scenario: Agendador serve PDF de caso processado por outro agendador

- **GIVEN** um caso com decisão de agendamento registrada por outro agendador (confirmação ou negação), em estado pós-decisão
- **WHEN** um agendador diferente requisita o serve ou o viewer
- **THEN** o documento é servido normalmente (equipe de agendamento)
