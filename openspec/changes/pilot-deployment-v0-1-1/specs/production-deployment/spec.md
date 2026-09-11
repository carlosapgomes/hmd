# production-deployment Specification (delta)

## Purpose

Runtime de produção do piloto (fase 1: login/admin/navegação; sem upload, workers e egress LLM desligados), com imagem implantável via GHCR e topologia de compose sobre infraestrutura compartilhada do hospital.

## ADDED Requirements

### Requirement: Runtime web de produção com healthchecks

O sistema SHALL servir a aplicação por gunicorn (CMD da imagem, logs em stdout) com estáticos coletados no build, e SHALL expor endpoints públicos sem dados de negócio `/healthz` (liveness, sem dependências) e `/readyz` (readiness, valida o banco sem ler dados). Quando o intake estiver desabilitado, nenhuma mutação de caso/upload pode ocorrer, mas login, admin, painel, manual e navegação seguem funcionando.

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

### Requirement: Configuração de produção fail-closed com cache de banco

Em produção o sistema SHALL usar cache compartilhado `DatabaseCache` na tabela `hmd_cache` (criada pelo passo de migração, idempotente), SHALL recusar inicialização com cache local por processo, e SHALL derivar hosts, origens CSRF e o header de proxy SSL de variáveis de ambiente compatíveis com um proxy HTTPS à frente, sem valores de segredo no código.

#### Scenario: Cache de banco em produção

- **GIVEN** settings de produção sem sobrescrita de cache
- **WHEN** a configuração é avaliada
- **THEN** o cache default é DatabaseCache na tabela hmd_cache e LocMem segue proibido

#### Scenario: Hosts e CSRF pelo ambiente

- **GIVEN** o piloto publicado atrás de proxy HTTPS no domínio configurado
- **WHEN** um POST autenticado é feito pela URL pública
- **THEN** passa pela verificação CSRF e a sessão é marcada segura

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
