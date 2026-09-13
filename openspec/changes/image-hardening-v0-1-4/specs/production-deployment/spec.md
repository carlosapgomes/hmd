# production-deployment Specification (delta)

## MODIFIED Requirements

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
