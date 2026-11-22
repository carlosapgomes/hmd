# Spec Delta

## MODIFIED Requirements

### Requirement: Resultado e casos encerrados visíveis ao criador

O detalhe do caso para o NIR SHALL exibir, a partir da decisão médica, o
resultado (decisões por procedimento com motivos, dados de agendamento e a
resposta final em destaque) e um meio de confirmar o recebimento apenas
enquanto o caso estiver em `FINAL_REPLY_POSTED` e for de sua autoria. A lista
"meus casos" SHALL listar **apenas casos ativos** (tudo exceto `CLEANED`) —
sem navegação Ativos/Encerrados; casos encerrados (do criador ou de qualquer
NIR) são alcançados pela página «Casos Encerrados» (busca global) e seu
detalhe histórico, onde o encerramento ADMINISTRATIVO também aparece com o
resultado "Encerrado administrativamente" e o motivo registrado.

#### Scenario: Resultado exibido ao criador

- **GIVEN** um caso com resposta final publicada, criado pelo NIR autenticado
- **WHEN** ele abre o detalhe do caso
- **THEN** decisões com motivos, dados de agendamento e a resposta final são exibidos, junto ao botão de confirmação de recebimento

#### Scenario: Ciência apenas do criador no estado certo

- **GIVEN** um caso em `FINAL_REPLY_POSTED` criado por outro NIR e um caso do NIR autenticado já encerrado
- **WHEN** ele tenta confirmar recebimento de qualquer um deles
- **THEN** recebe 404 no caso alheio e mensagem de erro no caso já encerrado, sem mudança de estado

#### Scenario: Aba de encerrados lista apenas casos CLEANED

- **GIVEN** 2 casos ativos e 1 encerrado (`CLEANED`) do NIR autenticado
- **WHEN** ele abre "meus casos"
- **THEN** apenas os 2 casos ativos são listados (o `CLEANED` não aparece; não existe mais navegação Ativos/Encerrados e o parâmetro legado `?tab=closed` cai na listagem de ativos preservando os filtros) e o caso encerrado é alcançável pela página «Casos Encerrados» (busca), que lista apenas `CLEANED` e intercorrências ativas

#### Scenario: Encerrado do criador alcançável pela busca

- **GIVEN** um caso `CLEANED` criado pelo NIR autenticado
- **WHEN** ele busca pelo nº na página «Casos Encerrados» e abre o detalhe histórico
- **THEN** o resultado (decisões, agendamento, resposta final, comunicações e trilha) é exibido em leitura; o redirect pós-ciência segue levando o criador ao detalhe do seu caso

#### Scenario: Encerramento administrativo visível ao criador

- **GIVEN** um caso do criador encerrado administrativamente
- **WHEN** o criador o localiza pela página «Casos Encerrados» e abre o detalhe histórico
- **THEN** o caso aparece com o resultado "Encerrado administrativamente" e o motivo registrado

### Requirement: Reenvio corrigido cria novo caso vinculado

O sistema SHALL permitir a **qualquer usuário com papel ativo `nir`** (criador ou não) reenviar, de forma corrigida, um caso encerrado: cria um novo caso que percorre o pipeline completo desde o início, vinculado ao original com motivo obrigatório e exatamente 1 PDF de relatório + um único tipo declarado explicitamente (nunca herdado; múltiplos tipos e múltiplos arquivos são rejeitados com erro nomeado, sem criar o caso). O usuário reenviador SHALL ser o criador do novo caso (`created_by`/`correction_created_by`), recebendo suas notificações e o dever de ciência do novo ciclo. O caso original SHALL permanecer inalterado exceto pelo evento de supersedição e pela listagem dos reenvios que o corrigem. A ação «Reenviar corrigido» SHALL estar disponível no detalhe histórico de casos `CLEANED` (qualquer criador) e no detalhe encerrado do criador; recusa para caso fora de `CLEANED`, sem qualquer escrita. A navegação da página de reenvio (breadcrumb, cancelamento e redirects de recusa) SHALL apontar ao detalhe histórico do original — acessível a qualquer papel `nir` —, nunca a rotas restritas ao criador; o redirect de sucesso leva o reenviador ao detalhe do novo caso (seu). Na listagem «Reenvios corrigidos» do caso original, o link para o caso corrigido SHALL respeitar a autorização do visitante: próprio → detalhe do caso; alheio e ativo → referência desambiguada sem link; alheio encerrado → detalhe histórico.

#### Scenario: Reenvio cria novo caso vinculado com motivo

- **GIVEN** um caso encerrado
- **WHEN** um usuário com papel ativo `nir` (criador ou não) o reenvia de forma corrigida com exatamente 1 PDF novo, tipo declarado e motivo
- **THEN** um novo caso é criado em processamento inicial vinculado ao original, ambos registram os eventos de correção e supersedição, e o reenviador é o criador do novo caso

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
- **THEN** o não encerrado é recusado com erro nomeado e nenhum caso é criado; o encerrado de outro criador é aceito (guarda por papel, não por posse — decisão do dono 2026-11-21)

#### Scenario: Reenvio com múltiplos arquivos ou tipos é rejeitado

- **GIVEN** um caso encerrado
- **WHEN** o NIR o reenvia de forma corrigida com mais de 1 PDF ou mais de um tipo declarado
- **THEN** nenhum caso é criado e o erro nomeado informa as restrições

#### Scenario: Navegação da página de reenvio para o não-criador

- **GIVEN** um NIR não-criador no formulário de reenvio de um caso `CLEANED` alheio, aberto a partir do detalhe histórico
- **WHEN** ele usa o breadcrumb ou «Cancelar», ou sofre recusa (estado incorreto / intake desabilitado)
- **THEN** todos os destinos são o detalhe histórico do original (ou a busca, como fallback do escopo), sem qualquer 404 de rota restrita ao criador; no sucesso, o redirect leva ao detalhe do novo caso, de sua autoria

#### Scenario: Link do caso corrigido respeita a autorização do visitante

- **GIVEN** um caso encerrado com reenvio corrigido criado por outro NIR
- **WHEN** o criador original vê a listagem «Reenvios corrigidos deste caso»
- **THEN** enquanto o novo caso está ativo, a referência desambiguada aparece SEM link (guard ativo preservado); quando o novo caso encerra (`CLEANED`), a referência passa a apontar ao detalhe histórico — o reenviador, por sua vez, sempre acessa seu próprio caso pelo detalhe
