# HMD — Hemodinâmica

Sistema de apoio à regulação de pacientes do serviço de hemodinâmica do HGRS.
Clone de padrões do `ats-web` (monolito Django SSR), com autenticação AD/Kerberos
em estágio posterior, anonimização Presidio fail-closed e catálogo de 13 tipos
de procedimento.

## Documentação

- `AGENTS.md` — regras, stack, comandos, política de testes, workflow OpenSpec.
- `PROJECT_CONTEXT.md` — contexto executivo, fontes autoritativas e roadmap
  dos 11 changes.
- `docs/adr/` — decisões arquiteturais (ADR-0001 a 0009).

## Stack

Python 3.13+ · Django 5.2+ · PostgreSQL 17+ · Bootstrap 5.3 · Vanilla JS · uv

## Status

**v0.1.7 — piloto de produção em operação (fase 1)**: autenticação por AD
(Kerberos), admin, painel, manual e navegação em `https://hmd.projetoshgrs.com`
atrás de Cloudflare→Caddy; imagem GHCR não-root (uid 10001), segredos 100% por
arquivo, intake/workers/egress LLM desligados (fase 1). Ciclo do caso completo
(`NEW→CLEANED`) implementado — upload com anexos, extração/anonimização
fail-closed, pipeline LLM por tipo (só tokens), decisão médica consultiva,
agendamento em 2 unidades com labels configuráveis, resposta final/ciência,
notificações, painel, PWA e manual. Guard de intranet por conjunto de papéis
com encerramento de sessão no bloqueio externo.
1160 testes · 14 specs (`openspec/`) · 18 changes arquivados. Veja `CHANGELOG.md`.

## Ambiente de desenvolvimento

Pré-requisitos: Python 3.13, `uv`.

```bash
# 1. Instalar dependências (cria .venv + uv.lock se necessário)
uv sync

# 2. Servidor de desenvolvimento
uv run python manage.py runserver --settings=config.settings.dev
#    Home estática mínima em http://127.0.0.1:8000/

# 3. Qualidade
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

Variáveis de ambiente documentadas em `.env.example` (`cp .env.example .env`).
Settings por ambiente em `config.settings.{base,dev,prod,test}`; `prod` falha
fechado sem `DJANGO_SECRET_KEY`.

## Autenticação AD (Kerberos) e admin local por design

Usuários provisionados com `ad_upn` (`cpf@dominio`, via Django admin)
autenticam exclusivamente via Active Directory (Kerberos AS-REQ com a senha
digitada). O realm é derivado do sufixo do UPN no momento da validação — não é
configurável. DCs e timeout vêm do ambiente (`AD_DCS`, `AD_KDC_TIMEOUT`).

O superusuário **sem** `ad_upn` autentica localmente em qualquer ambiente, por
design (ADR-0009): a credencial do AD é assistencial única no hospital e o
admin do sistema é uma **identidade local permanente** — não existe flag de
habilitação; preencher `ad_upn` no superusuário o torna autenticável por AD. O
login do Django admin é coberto pelo mesmo anti-lockout local dos demais
logins. Detalhes em `docs/adr/ADR-0004-*.md` e `docs/adr/ADR-0009-*.md`.

### `ad_check` — aceitação contra os DCs reais (manual, fora do CI)

Valida uma senha AD contra **cada** DC configurado e imprime por DC o
resultado (ok/código/razão/latência). A senha é lida via prompt (`getpass`) —
nunca em argumentos, variável de ambiente ou logs:

```bash
uv run python manage.py ad_check --cpf 12345678901@<dominio-ad>
```

Requer rede hospitalar; útil para a matriz de aceitação da pesquisa (senha
errada `24`, CPF inexistente `6`, DC `.19` fora → `.21`, etc.).

## Testes

A suíte usa settings próprias (`config.settings.test`) com banco de teste
dedicado (porta 5433), isolado do banco de desenvolvimento:

```bash
uv run pytest
```

## Benchmark de anonimização (aceite operacional)

O harness `anonymization_benchmark` avalia um corpus de textos em JSONL (uma
entrada por linha `{"text": ..., "expected": [{"value": ...,
"entity_type": ...}]}`) contra o núcleo de anonimização: recall por tipo de
entidade, contagens, latência p50/p95 por documento, varredura zero-PII no
output (CPF/CNS com checksum), pico de RSS do processo e documentos
bloqueados. O comando falha (exit ≠ 0) quando algum tipo fica abaixo do
recall mínimo, um vestígio de PII é encontrado, um documento bloqueia ou o
RSS excede o limite opcional:

```bash
# Corpus sintético versionado (roda na suíte; exit 0 esperado)
uv run python manage.py anonymization_benchmark \
  --corpus apps/anonymization/tests/fixtures/benchmark_corpus.jsonl \
  --settings=config.settings.dev

# Corpus real do serviço (pré-produção) — ajuste o mínimo por env
ANONYMIZATION_BENCHMARK_MIN_RECALL=0.90 \
  uv run python manage.py anonymization_benchmark --corpus corpora/reais.jsonl

# Limite duro opcional de RSS (MB; sem limite por default)
ANONYMIZATION_BENCHMARK_MAX_RSS_MB=900 \
  uv run python manage.py anonymization_benchmark --corpus corpora/reais.jsonl
```

O recall mínimo default é o setting `ANONYMIZATION_BENCHMARK_MIN_RECALL`
(0.90), sobreponível por `--min-recall`. Corpus sintético versionado acompanha
a suíte; a aceitação com relatórios reais é passo operacional manual,
pré-produção (ADR-0007).

## Primeiros testes (implantação interna)

O servidor de testes internos usa o compose de desenvolvimento (runserver +
workers). A produção do piloto (fase 1) roda o compose `docker-compose.prod.yml`
(gunicorn não-root + migrate/seeds one-shot), implantado por tag@digest do GHCR
— veja a seção de implantação abaixo.

```bash
# 1. Código + imagem (workers/pdf/anonymization/llm/attachments usam a
#    imagem pronta com o modelo spaCy pt_core_news_lg no build):
git clone https://github.com/carlosapgomes/hmd.git && cd hmd
docker compose -f docker-compose.yml -f docker-compose.dev.yml build

# 2. Ambiente (.env na raiz — modelo em .env.example):
#    - DJANGO_SECRET_KEY (obrigatório; gere um segredo real)
#    - credenciais do Postgres (ver .env.example)
#    - OPENROUTER_API_KEY + LLM_MODEL_LLM1/LLM2 (pipeline; sem elas os
#      casos ficam retidos fail-closed)
#    - VISION_MODEL (OCR externo de anexos; sem ela anexos de imagem
#      falham nomeados — extração local de PDFs segue)
#    - ALLOWED_HOSTS com o nome do servidor de teste

# 3. Banco + dados-base (idempotentes — re-executar é seguro):
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm web \
  python manage.py migrate --settings=config.settings.dev
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm web \
  python manage.py seed_admin --settings=config.settings.dev   # papéis + admin local
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm web \
  python manage.py seed_prompts --settings=config.settings.dev  # 29 prompts versionados

# 4. Sobe tudo (web + worker-pdf/anonymization/llm/attachments):
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Checklist pós-install: `manage.py ad_check` (se AD/Kerberos estiver no ar),
criar usuários de teste com os papéis (admin local → perfil), atribuir
especialidades aos médicos, e conferir o sino de notificações/painel/manual
na navbar. Os 13 tipos de procedimento vêm do catálogo; especialidades médicas
vêm da migration `0003` (idempotente).

Limitações conhecidas deste release: workers exigem `VISION_MODEL` para OCR
externo; benchmark com corpus real e revisão dos prompts são aceite
pré-produção (`CHANGELOG.md` → pendências).

## Piloto (fase 1)

O piloto roda por `docker-compose.prod.yml` (arquivo autônomo, change
`pilot-deployment-v0-1-1`): **web** (gunicorn, CMD do Dockerfile) + passo
one-shot **migrate**; nenhum worker ligado.

**Topologia.** O web não publica porta no host (`expose: 8000`): o **Caddy**
do servidor é o upstream na rede `hospital_ingress_hmd` pelo aliás estável
`hmd`, termina o TLS e injeta `X-Forwarded-Proto` (por isso
`PROXY_SSL_HEADER=true`). O PostgreSQL 17 é **compartilhado e externo** (rede
`hospital-db-hmd`, DB `app_hmd`) — o serviço de banco e seus aliases
pertencem a outro compose, fora deste repo. A rede `hospital_egress_hmd` é a
saída restrita: o web só sai para **AD/DNS/Kerberos**; egress OpenRouter/OCR
fica desligado na fase 1. Todo segredo é montado por **arquivo** read-only (um
por consumidor). Logs em stdout com rotação pelo log driver (`json-file`,
10m × 3).

```bash
# 0. Segredos: crie os arquivos (uma senha por linha, FORA do Git) em
#    ./secrets/ (já ignorado) ou aponte APP_DB_PASSWORD_FILE /
#    MIGRATOR_DB_PASSWORD_FILE / SECRET_KEY_FILE / SUPERUSER_PASSWORD_FILE
#    para os arquivos no .env do host.

# 1. Migração + seeds (one-shot, credencial de MIGRATOR):
#    migrate && createcachetable hmd_cache && seed_admin && seed_prompts &&
#    seed_procedure_catalog — todos já no command do serviço.
docker compose -f docker-compose.prod.yml --profile migrate run --rm migrate

# 2. Web (healthcheck /readyz/ — a barra é obrigatória: sem ela o teste
#    seguiria um 301 e não provaria a prontidão real):
docker compose -f docker-compose.prod.yml up -d web

# 3. Workers do django-q2 (pdf/anonymization/llm/attachments) ficam
#    DESLIGADOS — só sobem com `--profile workers`, na próxima mudança.
```

**Atualização para a v0.1.4 — volume de mídia pré-existente.** A imagem passa a
executar como usuário dedicado não-root (uid/gid **10001**) e cria `/app/media`
com esse ownership. Um volume **novo** herda esse ownership na primeira
montagem; o `media_data` **já existente** (criado por um deploy anterior à
v0.1.4) continua root-owned — o ownership do mountpoint da imagem NÃO é
aplicado a volume que já existe. A fase 1 não escreve mídia (intake desligado),
então o `up` da v0.1.4 funciona sem ajuste **de mídia**; antes de **ligar os uploads
(fase 2)** rode o re-chown pontual. `--cap-add CHOWN` é obrigatório porque o
serviço derruba TODAS as capabilities (`cap_drop: ALL`) e sem `CAP_CHOWN` o
`chown` falha com *Operation not permitted*:

```bash
# Uma vez, antes de ativar o intake na fase 2 (idempotente; em volume novo é
# inócuo — ele já nasce com o ownership correto):
docker compose -f docker-compose.prod.yml run --rm --user root --cap-add CHOWN \
  web chown -R 10001:10001 /app/media
```

A leitura dos segredos segue a mesma regra: o compose monta cada arquivo como
bind do host (sem `mode:`/`uid:` fora de swarm), **preservando modo/owner**,
então o uid/gid **10001** precisa poder lê-los. O modo default de `echo`/editor
(**0644**) funciona; um arquivo `0600` root-owned aborta a inicialização como
não-root com `ImproperlyConfigured` (*Não foi possível ler o segredo em ...*).

**Segredos por arquivo.** Nenhum segredo trafega em variável de ambiente no
compose: cada serviço monta Docker secrets read-only em `/run/secrets/` e
aponta o `*_FILE` correspondente — o arquivo tem **precedência** sobre
qualquer env equivalente e falha fechado (inicialização aborta) se estiver
ilegível ou vazio. Consumidor por segredo: **web/workers** usam `secret_key`
+ `app_db_password`; o passo **migrate** usa `secret_key` +
`migrator_db_password` + `superuser_password`. Crie os arquivos em
`./secrets/` (default do compose, já no `.gitignore`) ou aponte
`APP_DB_PASSWORD_FILE`/`MIGRATOR_DB_PASSWORD_FILE`/`SECRET_KEY_FILE`/
`SUPERUSER_PASSWORD_FILE` para outro caminho no `.env` do host. Como o bind
preserva modo/owner do host, o uid/gid **10001** precisa ler cada arquivo —
ajuste se o seu host criar `0600`:

```bash
# 0600 root-owned aborta a inicialização non-root (0644 é o default de echo).
# Caminho default ./secrets/ — ajuste se você usa *_FILE customizado:
chmod 0644 secrets/*.txt
# Alternativa sem expor o segredo a outros usuários do host (todos os
# consumidores de prod rodam como 10001):
chown 10001:10001 secrets/*.txt
```

**Configuração (`.env` no host, fora do Git).** Nomes novos documentados em
`.env.example`: `HMD_IMAGE_TAG` (**obrigatório pinado tag+digest**:
`v0.1.7@sha256:cd4942bc535f3bd805657b9aa4896c7922a59e0cdee829bc8af73e640285654a`), as envs de arquivo de segredo acima, os nomes de
banco `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER` (+ `MIGRATOR_DB_USER`, role DDL
usada só pelo passo migrate) — o compose não usa mais URL de banco —,
`CSRF_TRUSTED_ORIGINS`, `PROXY_SSL_HEADER` e `DJANGO_SUPERUSER_USERNAME` (env
não-sensível; a senha vem do arquivo). **Obrigatórios e não default**: os
arquivos de segredo reais (a chave de exemplo é pública e serve só p/ dev),
`DB_HOST`/`DB_NAME` apontando o DB `app_hmd` com a credencial da APLICAÇÃO
(distinta da migrator; a role da aplicação precisa de **DML** nas tabelas do
schema, incluindo `hmd_cache`), `ALLOWED_HOSTS=hmd.projetoshgrs.com` (sem
ele, a URL pública dá 400 mesmo com o container healthy — o healthcheck
interno usa 127.0.0.1) e o username do superusuário do seed — sem ele o passo
1 aborta antes de semear os prompts (o `&&` do one-shot é sequencial).
(`CSRF_TRUSTED_ORIGINS`/`PROXY_SSL_HEADER` têm default no compose, mas
defina-os explicitamente para não depender disso.) Use
`DB_HOST=postgres-app-hmd` (alias inequívoco do PG na rede do banco) — **nunca**
`hmd`, que na rede de ingress é o alias do próprio web (ambiguidade de DNS
Docker entre redes).

**Pré-requisitos de infraestrutura** (fora deste repo): as 3 redes externas
(`hospital-db-hmd`, `hospital_ingress_hmd`, `hospital_egress_hmd`) e o
PostgreSQL compartilhado (DB `app_hmd` + aliases `postgres-app-hmd`/`hmd`)
devem existir ANTES do `up`; e o host precisa de credencial para puxar a
imagem do GHCR (`docker login ghcr.io` com PAT `read:packages`) — ou o
pacote deve ser público. Ajustes do
piloto: `ALLOWED_HOSTS=hmd.projetoshgrs.com` (o compose acrescenta
`127.0.0.1` automaticamente p/ o healthcheck), `AD_DCS` e
`INTRANET_IP_RANGE` (valores reais fora do Git).
`INTRANET_RESTRICTED_ROLES` tem default `nir` no compose (iguais ao ATS).
O guard avalia o **IP reportado pelo Cloudflare** (`Cf-Connecting-IP`): se
usuários internos acessarem pela URL pública, o IP visto é o de **egress de
internet do hospital** — inclua essa faixa no `INTRANET_IP_RANGE`; acessos
sem o header (LAN direta ao Caddy) caem em `REMOTE_ADDR` (IP do proxy).
`TRUSTED_PROXY_HEADER=HTTP_CF_CONNECTING_IP` (default) — na topologia do
piloto (Cloudflared → Caddy → HMD) a borda Cloudflare fixa esse header com o
IP real do cliente e o Caddy apenas o repassa: **não** configure
`header_up X-Forwarded-For {remote_host}` nesse hop (gravaria o IP do
cloudflared e o guard de intranet nunca casaria). Sobrescrever XFF só vale
em topologia Caddy-direto (cliente → Caddy → HMD).
`INTAKE_ENABLED` fica **false** (fase 1: nenhum relatório enviado; criação
de casos/reenvios bloqueada no boundary do serviço).

**Limites do envio de relatórios (fase 2).** Cada PDF é o relatório de **um
paciente** e vira **um caso**; o tipo de procedimento é **único por envio** e
anexos de evidência só valem com **exatamente 1 relatório**. Três envs
tunam o lote (defaults entre parênteses): `INTAKE_MAX_FILES_PER_BATCH` (30
arquivos), `INTAKE_MAX_UPLOAD_BYTES_PER_FILE` (20971520 = 20 MB por arquivo) e
`INTAKE_MAX_UPLOAD_BYTES_PER_BATCH` (104857600 = 100 MB no total do envio).
O total de **100 MB** é o limite prático do request body no **túnel Cloudflare**
do piloto (~100 MB no plano free) — suba só se o caminho de entrada mudar. O
`docker-compose.prod.yml` repassa as três com `${VAR:-default}` (nunca string
vazia, que anularia o default das settings). No lote, arquivo inválido/acima do
limite vira erro nomeado e os válidos seguem: a página de resultado lista os
casos criados e os arquivos rejeitados.

**Caddy.** O alvo do upstream é o **aliás estável `hmd:8000`** na rede
`hospital_ingress_hmd` (declarado em `networks.hospital_ingress_hmd.aliases`
do serviço `web` — não use o nome gerado pelo compose, que muda junto com o
projeto) e o healthcheck do próprio compose em `/readyz/`.
