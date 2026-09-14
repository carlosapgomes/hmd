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

### Requirement: Workers do pipeline com segredo por arquivo e limites de recurso

Os workers do pipeline SHALL receber a chave da OpenRouter exclusivamente por
arquivo de segredo (montagem read-only do secret `openrouter_api_key`), com
suporte a `OPENROUTER_API_KEY_FILE` nas settings com precedência sobre a
variável de ambiente plana e comportamento fail-closed para arquivo ilegível
ou vazio. Apenas os processos que chamam a OpenRouter (`worker-llm` e
`worker-attachments`) SHALL receber a chave e as variáveis de modelo que
consomem (`worker-llm`: `LLM1_MODEL` e `LLM2_MODEL`; `worker-attachments`:
`LLM1_MODEL` e `VISION_MODEL`); os demais serviços — inclusive o `web`, que
usa a rede de egress apenas para AD/DNS/Kerberos — SHALL permanecer sem
qualquer variável da OpenRouter. O `worker-anonymization` SHALL permitir
trocar o modelo spaCy (`ANONYMIZATION_SPACY_MODEL`) por variável de ambiente,
sem receber a chave. Cada worker SHALL declarar limite de memória ajustável
por variável de ambiente no host, orçado pelo número de processos do cluster. A ativação do
intake SHALL permanecer controlada por `INTAKE_ENABLED` (default `false`),
de modo que a configuração dos workers não ative o pipeline por si só.

#### Scenario: Chave OpenRouter só por arquivo nos workers com egress

- **GIVEN** o compose de produção resolvido com todos os profiles
- **WHEN** os serviços `worker-llm` e `worker-attachments` são inspecionados
- **THEN** ambos montam o secret `openrouter_api_key` e declaram
  `OPENROUTER_API_KEY_FILE` apontando para a montagem, sem a chave em
  variável de ambiente plana

#### Scenario: Serviços que não chamam a OpenRouter não recebem a chave

- **GIVEN** o compose de produção resolvido com todos os profiles
- **WHEN** os serviços `web`, `worker-pdf`, `worker-anonymization` e
  `migrate` são inspecionados
- **THEN** nenhum deles declara `OPENROUTER_API_KEY`,
  `OPENROUTER_API_KEY_FILE` ou variáveis de modelo — inclusive o `web`, que
  partilha a rede de egress apenas para AD/DNS/Kerberos

#### Scenario: Arquivo de chave tem precedência e falha fechado

- **GIVEN** as settings de produção com `OPENROUTER_API_KEY_FILE` apontando
  para um arquivo legível e não vazio
- **WHEN** as settings são carregadas
- **THEN** `OPENROUTER_API_KEY` vem do arquivo; se o arquivo estiver vazio ou
  ilegível, o carregamento falha com `ImproperlyConfigured` (nunca cai
  silenciosamente para a env plana)

#### Scenario: Cada worker com limite de memória ajustável

- **GIVEN** o compose de produção resolvido com o profile `workers`
- **WHEN** os 4 workers são inspecionados
- **THEN** cada um declara `mem_limit` com valor passável por variável de
  ambiente e default não vazio, orçado pelo número de processos do cluster
  (o `worker-anonymization` cobre as duas instâncias do engine de
  anonimização)

#### Scenario: Configurar workers não ativa o intake

- **GIVEN** o compose de produção com o secret e os modelos configurados no
  host, sem `INTAKE_ENABLED` definida
- **WHEN** o stack sobe com o profile `workers`
- **THEN** o envio de relatórios permanece desligado até
  `INTAKE_ENABLED=true` no serviço `web`

#### Scenario: Rollback da fase 2 por variável de ambiente

- **GIVEN** a fase 2 ativa (intake ligado e workers rodando)
- **WHEN** `INTAKE_ENABLED` volta a `false` e o profile `workers` é
  desativado
- **THEN** o serviço `web` volta a recusar envios e os casos já criados
  permanecem íntegros com seus estados

#### Scenario: Modelo spaCy do worker de anonimização é ajustável por env

- **GIVEN** o compose de produção resolvido com o profile `workers`
- **WHEN** o serviço `worker-anonymization` é inspecionado
- **THEN** ele declara `ANONYMIZATION_SPACY_MODEL` com default
  `pt_core_news_lg` e sem nenhuma variável da OpenRouter
