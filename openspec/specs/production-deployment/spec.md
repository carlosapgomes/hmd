# production-deployment Specification

## Purpose

Runtime de produção do piloto (fase 1: login/admin/navegação; sem upload, workers e egress LLM desligados), com imagem implantável via GHCR e topologia de compose sobre infraestrutura compartilhada do hospital.

## Requirements

### Requirement: Runtime web de produção com healthchecks

O sistema SHALL servir a aplicação por gunicorn (CMD da imagem, logs em stdout) com estáticos coletados no build, **executando como usuário dedicado não-root de uid fixo** (sem necessidade de root em runtime: rootfs somente-leitura, `/tmp` tmpfs, escritas de estado no banco), e SHALL expor endpoints públicos sem dados de negócio `/healthz` (liveness, sem dependências) e `/readyz` (readiness, valida o banco sem ler dados). Quando o intake estiver desabilitado, nenhuma mutação de caso/upload pode ocorrer, mas login, admin, painel, manual e navegação seguem funcionando.

#### Scenario: healthz sem dependências

- **GIVEN** a aplicação em execução
- **WHEN** `GET /healthz`
- **THEN** responde 200 com status ok, sem exigir autenticação nem tocar o banco

#### Scenario: readyz reflete o banco

- **GIVEN** o banco acessível
- **WHEN** `GET /readyz`
- **THEN** responde 200 com status ready
- **GIVEN** o banco inacessível
- **WHEN** `GET /readyz`
- **THEN** responde 503 com status unavailable

#### Scenario: Processo web roda como não-root

- **GIVEN** a imagem de produção em execução (web e serviços one-shot do migrate)
- **WHEN** o processo serve requisições e executa os comandos de migração/seeds
- **THEN** tudo opera sob o uid do usuário dedicado, sem privilégio de root e sem escrita fora de `/tmp` e do volume de mídia

### Requirement: Configuração de produção fail-closed com cache de banco

Em produção o sistema SHALL usar cache compartilhado `DatabaseCache` na tabela `hmd_cache` (criada pelo passo de migração, idempotente), SHALL recusar inicialização com cache local por processo, SHALL derivar hosts, origens CSRF e o header de proxy SSL de variáveis de ambiente compatíveis com um proxy HTTPS à frente, sem valores de segredo no código, e o entrypoint WSGI SHALL assumir settings de produção por default (a variável `DJANGO_SETTINGS_MODULE` explícita continua vencendo; ausência de configuração cai no modo mais restritivo, nunca em settings de desenvolvimento).

#### Scenario: Cache de banco em produção

- **GIVEN** settings de produção sem sobrescrita de cache
- **WHEN** a configuração é avaliada
- **THEN** o cache default é DatabaseCache na tabela hmd_cache e LocMem segue proibido

#### Scenario: Hosts e CSRF pelo ambiente

- **GIVEN** o piloto publicado atrás de proxy HTTPS no domínio configurado
- **WHEN** um POST autenticado é feito pela URL pública
- **THEN** passa pela verificação CSRF e a sessão é marcada segura

#### Scenario: WSGI sem variável de ambiente cai em produção

- **GIVEN** a imagem executando o entrypoint WSGI sem `DJANGO_SETTINGS_MODULE` definida
- **WHEN** a aplicação WSGI é inicializada
- **THEN** os settings carregados são os de produção (config.settings.prod), nunca os de desenvolvimento

### Requirement: Intake desabilitável fail-closed

O sistema SHALL suportar desabilitar a criação de novos casos/uploads por configuração de ambiente (`INTAKE_ENABLED`), com default desligado em produção e ligado em desenvolvimento/teste. Com o intake desligado, NENHUM caso, documento, anexo ou tarefa pode ser criado ou enfileirado pelas rotas de intake — a mutação é bloqueada no boundary do serviço com erro nomeado, e a rota informa o usuário sem efeito colateral.

#### Scenario: POST de criação bloqueado sem efeito

- **GIVEN** `INTAKE_ENABLED` desligado
- **WHEN** o NIR submete o form de criação de caso
- **THEN** nenhuma row de caso/documento/anexo é criada, nenhuma tarefa é enfileirada e a resposta informa que o envio está desabilitado

#### Scenario: Serviço fail-closed para qualquer caller

- **GIVEN** `INTAKE_ENABLED` desligado
- **WHEN** o serviço de criação de caso (ou reenvio/reprocessamento de documentos) é invocado diretamente
- **THEN** levanta erro nomeado antes de qualquer escrita ou gravação de arquivo

### Requirement: Release de imagem implantável no GHCR

O repositório SHALL publicar, em cada tag `v*`, uma imagem Docker `linux/amd64` da aplicação no GHCR do projeto com a tag da versão, pronta para o compose de produção, sem segredos embutidos.

#### Scenario: Tag publica imagem amd64

- **GIVEN** a tag `v0.1.1` pushed
- **WHEN** o workflow de release executa
- **THEN** a imagem amd64 aparece no GHCR com a tag v0.1.1 e digest reportável

### Requirement: Segredos por arquivos no deploy produtivo

O deploy de produção SHALL fornecer todos os valores secretos (senha do banco da aplicação e do migrator, chave secreta do Django e senha do admin local inicial) por arquivos montados como segredos read-only, um consumidor por segredo, sem senha em variável de ambiente ou em `DATABASE_URL`, e sem qualquer valor secreto no repositório. O carregamento SHALL ser fail-closed: arquivo ausente/ilegível/vazio, ou ausência de qualquer fonte, aborta a inicialização com erro nomeado; o arquivo tem precedência sobre a variável de ambiente correspondente quando ambas existirem.

#### Scenario: Segredo lido de arquivo com precedência

- **GIVEN** a variável apontando para um arquivo de segredo válido e a variável de ambiente com a senha também definida
- **WHEN** as configurações são carregadas
- **THEN** o valor usado é o do arquivo

#### Scenario: Falha fechada sem fonte de segredo

- **GIVEN** nem o arquivo de segredo nem a variável de ambiente correspondente definidos em produção
- **WHEN** as configurações são carregadas
- **THEN** a inicialização aborta com erro nomeado indicando a fonte esperada

#### Scenario: Consumidores isolados por segredo

- **GIVEN** o compose de produção renderizado
- **WHEN** os segredos são inspecionados
- **THEN** a senha do migrator só está montada no serviço de migração, a senha do admin só no serviço de migração, e a senha da aplicação e a chave secreta apenas nos serviços que as consomem — nenhuma URL de banco com senha no environment
