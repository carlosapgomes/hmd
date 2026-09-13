# Changelog

Formato: versões com resumo por change (Keep a Changelog adaptado ao workflow
OpenSpec — cada change tem proposal/design/slices/specs arquivados em
`openspec/changes/archive/`).

## [0.1.5] — 2026-09-13

**UX de segurança do guard de intranet** (change `intranet-blocked-logout`,
de uso real do piloto): usuário exclusivamente restrito (`nir` puro)
bloqueado de fora da intranet tinha a sessão mantida e ficava preso — a
página de bloqueio era texto puro (sem navbar/logout) e `/login/`
redirecionava autenticados de volta à home bloqueada. Agora o guard **encerra
a sessão** no bloqueio e responde 403 com página própria e botão "Voltar ao
login" (funciona: anônimo → form). Cookie expirado na própria resposta
(max-age 0, assertado); log de auditoria `session_terminated=1`; multi-role
com papel externo segue acessando normalmente (regra de conjunto).
Housekeeping: README atualizado do "v0.1.0" para o estado real do piloto;
comentário stale do workflow. Baseline 1101 testes.

## [0.1.4] — 2026-09-13

**Hardening do runtime do piloto + carregamento dos changes pós-piloto**: a
imagem de produção passa a executar como usuário dedicado **não-root**
(uid/gid 10001) e o entrypoint WSGI passa a assumir settings de **produção**
por default (fail-safe). Entram também os dois changes fechados depois do
deploy do piloto: guard de intranet decidido pelo **conjunto** de papéis e
rótulos das unidades configuráveis por ambiente.

### Runtime de produção (hardening)

- **Imagem não-root** (`image-hardening-v0-1-4`, slice 002): o `Dockerfile`
  cria o grupo/usuário `hmd` (uid/gid **10001**, sem shell) e declara
  `USER 10001:10001` depois do `collectstatic` do build. `/app/media` nasce na
  imagem com o ownership do usuário, de modo que o volume nomeado (web e
  workers) o herde na primeira montagem. Nada em runtime exige root (gunicorn
  em 8000, secrets montados legíveis pelo uid 10001 (modo 0644; 0600
  root-owned aborta), `/tmp` em tmpfs 1777, estado no banco) e o comentário de
  dívida "non-root fica para o próximo release" **saiu** do compose —
  `read_only`, `tmpfs`, `no-new-privileges` e `cap_drop: ALL` seguem como
  defesa em profundidade.
- **WSGI fail-safe** (`image-hardening-v0-1-4`, slice 001): `config/wsgi.py`
  sem `DJANGO_SETTINGS_MODULE` assume `config.settings.prod` (a env explícita
  continua vencendo; `manage.py` mantém o default de dev para a CLI).
- **Desenvolvimento**: com o `USER` da imagem o fluxo dev quebraria (venv
  root-owned criado pelo `uv sync`); o `docker-compose.dev.yml` passa a
  declarar **deliberadamente** `user: "0:0"` nos 5 serviços de imagem
  (dev/teste como root, produção non-root), travado por teste estático.

### Changes pós-piloto carregados

- **Guard de intranet por CONJUNTO de papéis** (`intranet-any-role-egress`):
  o bloqueio externo vale apenas quando **todos** os papéis do usuário estão
  em `INTRANET_RESTRICTED_ROLES` — `nir` puro continua 403 fora da intranet,
  e quem tem algum papel externo (`{nir, manager}` com papel ativo nir, por
  exemplo) passa sem trocar de papel. ADR-0002 revisado.
- **Rótulos das unidades configuráveis** (`unit-labels-env`): envs
  `HMD_UNIT_1_LABEL`/`HMD_UNIT_2_LABEL` (defaults `Unidade 1`/`Unidade 2`) com
  fonte única (`apps/cases/units.py`) em toda a superfície — form, dashboard,
  fila/detalhes do agendador, detalhes do NIR, resposta final ao NIR (texto da
  unidade 2 **interpolado** com o rótulo configurado) e manual. O histórico de
  comunicações preserva o texto canônico da época da postagem.

### Deploy (piloto fase 1)

- **Pins/default na v0.1.4**: `${HMD_IMAGE_TAG:-v0.1.4}` no
  `docker-compose.prod.yml` e exemplos `v0.1.4@sha256:<digest>` no `README.md`
  e no `.env.example` (o digest é preenchido na publicação do owner).
- **Upgrade de volume pré-existente**: o volume `media_data` criado pela
  v0.1.3 é **root-owned** e NÃO herda o ownership do mountpoint da imagem nova
  (só volumes novos herdam). A fase 1 não escreve mídia (intake desligado),
  mas **antes de ligar os uploads (fase 2)** rode o re-chown pontual
  documentado no README: `docker compose -f docker-compose.prod.yml run --rm
  --user root --cap-add CHOWN web chown -R 10001:10001 /app/media`
  (`--cap-add CHOWN` é necessário porque o serviço derruba todas as
  capabilities).

**Baseline**: 1099 testes · ruff/format/mypy limpos.

## [0.1.3] — 2026-09-12

**Correção de falha real do migrate do piloto**: o backend de cache de
produção apontava para `django.core.cache.backends.database.DatabaseCache`
(path de import inválido — o módulo é `...backends.db`). Settings não
importam a string no load, então os asserts antigos passavam e o erro só
aparecia no `createcachetable`/runtime (`InvalidCacheBackendError`, rc=1
antes de tabelas/seeds; rollback local, app greenfield — sem dado perdido).
Regressões novas: `import_string` do backend de prod; `manage.py check`
com settings de prod (subprocess, envs dummy + secret em arquivo); roundtrip
`createcachetable` + set/get com `db.DatabaseCache` no banco de teste.
Baseline 1079 testes.

## [0.1.2] — 2026-09-11

**Adendos pós-release (compose/docs — sem nova imagem; a imagem v0.1.2 permanece
a referência de deploy)**: (a) topologia Cloudflared→Caddy→HMD — default
`TRUSTED_PROXY_HEADER=HTTP_CF_CONNECTING_IP` (Caddy repassa sem tocar; sem
`header_up X-Forwarded-For` nesse hop); (b) limites PLANEJADOS/INICIAIS de
fase 1 do web (512m / 1.0 cpu / 200 pids — revisar por observação; workers
exigirão faixas próprias) + hardening de container (rootfs read-only,
tmpfs /tmp 64m, no-new-privileges, cap_drop ALL) mitigando o risco residual
de root aceito na fase 1 (non-root fica p/ v0.1.3); (c) **correção P1**:
`DJANGO_SETTINGS_MODULE=config.settings.prod` no web (sem ela, o wsgi.py
defaulta para settings de DEV — DEBUG=True — pois o gunicorn não aceita
`--settings`); (d) verificação: extensões `unaccent`/`pg_trgm` do init.sql
de dev/teste não são usadas por código/migrations (normalização do
prior-case é NFKD puro em Python) — produção não precisa delas nem de
grants de EXECUTE.

Correções obrigatórias do blueprint (owner) para o piloto no HGRS: alias
estável do web, **todos** os segredos por arquivos read-only e `latest` fora
dos releases futuros.

### Deploy (piloto fase 1)

- **Alias estável `hmd`**: o serviço `web` declara
  `networks.hospital_ingress_hmd.aliases: ["hmd"]` — o upstream do Caddy é
  **sempre `hmd:8000`**, independente do nome gerado pelo compose (o README
  deixa de citar `hmd-prod-web-1:8000`).
- **Segredos por ARQUIVO** (`docker-compose.prod.yml`): top-level `secrets`
  (`app_db_password`, `migrator_db_password`, `secret_key`,
  `superuser_password`; caminhos configuráveis por `*_FILE`, default
  `./secrets/`) montados read-only, **um consumidor por segredo**: web/workers
  usam `secret_key` + `app_db_password`; o passo migrate usa `secret_key` +
  `migrator_db_password` + `superuser_password`. `DATABASE_URL`/
  `MIGRATOR_DATABASE_URL` saem do compose inteiro — o banco é montado por
  `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER` (+ `DB_PASSWORD_FILE`; o migrate usa
  `MIGRATOR_DB_USER`).
- **Runbook**: imagem pinada por **tag+digest**
  (`HMD_IMAGE_TAG=v0.1.2@sha256:<digest>`); `.gitignore` passa a ignorar
  `secrets/`.

### Configuração

- **`SECRET_KEY` por arquivo**: `config.settings.prod` lê
  `DJANGO_SECRET_KEY_FILE` (arquivo com **precedência** sobre
  `DJANGO_SECRET_KEY`; ilegível/vazio aborta com `ImproperlyConfigured`).
- **`DATABASES` de produção** resolvidas por `database_config` (`DB_*` +
  `DB_PASSWORD_FILE`), sem depender de `DATABASE_URL` no ambiente.
- **`seed_admin`** aceita `DJANGO_SUPERUSER_PASSWORD_FILE` (mesma semântica de
  precedência/fail-closed; o username segue em env não-sensível).

### Release

- **Sem `latest`**: o workflow publica apenas a tag da versão nos releases
  futuros — a imagem do piloto é pinada por tag+digest.

**Baseline**: 1074 testes · ruff/format/mypy limpos.

## [0.1.1] — 2026-09-11

Primeiro release **de produção** do HMD para o piloto no HGRS: runtime web em
imagem versionada, healthchecks, settings endurecidas e o compose produtivo.
O upload do NIR (intake) fica **desligado** nesta fase.

### Runtime de produção

- **gunicorn** (`>=23,<24`) como servidor WSGI do `CMD` da imagem (3 workers ×
  2 threads, timeout 60s / graceful 30s, access e error log em stdout/stderr —
  a rotação é do log driver do compose).
- **`collectstatic` no build** da imagem (estáticos + manifest do WhiteNoise
  dentro dela, com envs dummy de build); nenhuma etapa de estáticos no startup.
- **`/healthz`** (liveness, sem tocar o banco) e **`/readyz`** (readiness:
  `SELECT 1` + validação do cache `hmd_cache`) — públicas, isentas do guard de
  intranet e usadas pelo healthcheck do compose.

### Configuração e hardening

- **Cache `DatabaseCache`** em `hmd_cache` (tabela criada pelo passo migrate
  one-shot via `createcachetable`); guard anti-LocMem mantido em produção.
- **Settings env-driven**: `CSRF_TRUSTED_ORIGINS` (default
  `https://hmd.projetoshgrs.com`), `SECURE_PROXY_SSL_HEADER` (via
  `PROXY_SSL_HEADER`, Caddy termina o TLS) e `ALLOWED_HOSTS`.
- **`INTAKE_ENABLED` fail-closed**: criação de casos bloqueada no boundary de
  serviço (erro nomeado antes de qualquer validação/escrita/arquivo/enqueue) e
  nos POSTs do intake (flash + redirect); default **false** em produção e true
  em dev/teste.

### Deploy (piloto fase 1)

- **`docker-compose.prod.yml`** autônomo: web GHCR sem porta no host (Caddy
  como upstream na rede de ingress), PostgreSQL 17 compartilhado e externo
  (DB `app_hmd`), redes externas (`hospital-db-hmd`,
  `hospital_ingress_hmd`, `hospital_egress_hmd`), **migrate one-shot**
  (`migrate && createcachetable hmd_cache && seeds`, com credencial de
  migrator) e workers django-q2 sob profile **desligado**; secrets apenas por
  `${VAR}`, fora do Git.
- **Imagem `ghcr.io/carlosapgomes/hmd:v0.1.1`** publicada pelo workflow de
  release (linux/amd64, tag da versão + `latest`) no push da tag `v0.1.1`.

### Notas

- A ativação de **intake**, dos **workers** (pdf/anonymization/llm/attachments)
  e do **egress OpenRouter/OCR** (`OPENROUTER_API_KEY`/`VISION_MODEL`) será uma
  **outra mudança**; a fase 1 opera o ciclo assistencial com o upload bloqueado.

**Baseline**: 1061 testes · ruff/format/mypy limpos.

## [0.1.0] — 2026-09-10

Primeiro release do HMD para **testes internos** no HGRS: ciclo completo do
caso de ponta a ponta (`NEW→CLEANED`) com anonimização fail-closed, pipeline
LLM por tipo de exame, decisão médica consultiva, agendamento em 2 unidades,
anexos com OCR híbrido auditado e verificação de paciente, notificações
in-app, painel gerencial zero-PHI, PWA instalável (ícone HMD) e manual de
usuário por papel.

**Baseline**: 1037 testes · ruff/format/mypy limpos · 12 archives · 13 specs
promovidas.

### Changes (ordem de execução)

- **bootstrap-django-hmd-core** — scaffold Django 5.2 + uv, settings por
  ambiente (base/dev/test/prod), toolchain de qualidade, compose de
  desenvolvimento e de teste, contas com multi-role e papel ativo, guard de
  intranet.
- **ad-kerberos-authentication** — login AD por CPF+senha no form (AS-REQ
  server-side via minikerberos pinado, provisionamento por UPN no admin,
  failover entre DCs por transporte; sem SPNEGO/keytab), lockout/rate-limit
  anti-brute-force; admin é identidade local por design (emenda
  `admin-local-identity`: gate e flag extintos, ADR-0009).
- **case-core-fsm-procedures** — FSM de 17 estados com transições protegidas
  (django-fsm-2), catálogo de 13 tipos de procedimento, procedimentos por
  caso atômicos, locks de concorrência, trilha de eventos append-only e
  thread de comunicações (user/system).
- **intake-nir-upload** — upload do relatório (1..N PDFs) pelo NIR com
  gate de qualidade (formato/OCR/padrão), worker de extração (cluster pdf),
  "Meus casos" com detalhe por criador.
- **presidio-anonymization** — anonimização determinística + Presidio
  (pt-BR, spaCy lg), pseudônimos `<CATEGORIA_N>` com linkage do caso,
  re-identificação na renderização, worker próprio, benchmark com corpus
  sintético versionado.
- **llm-pipeline-per-type** — cliente OpenRouter, prompts versionados
  (seeds idempotentes), LLM1 de extração e LLM2 de sumário/policy por tipo
  de exame (schemas strict), sugestão consultiva com motivos, prior-case
  (7d nº registro / 15d nome+nascimento), orchestrator fail-closed no
  cluster llm; **LLM só vê texto anonimizado** (asserts recursivos).
- **doctor-queue-decision** — fila médica por especialidade (generalista
  vê tudo), presenter re-identificado na renderização (identificação,
  história, timeline, alertas, prior-case, requisitos gerais), decisão por
  procedimento com motivo obrigatório na negativa.
- **scheduler-multi-unit** — fila do agendador (abas aguardando/decididos),
  confirmação/negação por unidade (unidade 1 com local/data parametrizados;
  unidade 2 texto fixo), PDF do relatório por posição (gate
  `scheduled_by==user` + status pós-decisão; 404 fail-closed), resposta
  final ao NIR por unidade, reabertura por intercorrência (só unidade 1).
- **nir-result-closure** — resposta final de negativa médica (motivos
  reais por procedimento), resultado e casos encerrados ao criador,
  ciência do NIR com limpeza transacional e minimização (documentos e
  artefatos clínicos removidos; identificação, decisões, trilha,
  comunicações e agendamento preservados; prior-case continua elegível),
  reenvio corrigido como novo caso vinculado com motivo obrigatório.
- **attachment-processing-ocr** — anexos jpg/png/pdf na criação e no
  reenvio (limites de contagem/tamanho/MIME), OCR híbrido (PDF com texto
  local via PyMuPDF; foto/scan/PDF-imagem via `VISION_MODEL` externo com
  evento de auditoria antes do envio — anuência formal registrada),
  anonimização no namespace de tokens do caso (semeadura) e verificação
  LLM de paciente **só com tokens** (`match|mismatch|unknown`;
  mismatch = alerta consultivo, nunca descarta), cards re-identificados na
  decisão médica, ciência remove anexos (rows+arquivos).
- **dashboard-notifications-pwa** — notificações in-app por marcos
  (resposta final e reabertura → criador; pronto para agendamento → todos
  os agendadores ativos) com sino/badge, lista com janela de 48h e redirect
  por papel ativo; painel gerencial por período (hoje/7d/30d/tudo) com
  quebra por tipo/unidade e tempo médio até decisão (fontes imutáveis,
  zero-PHI, acesso transversal); PWA instalável (manifest HMD, ícones
  HMD gerados por script, service worker conservador com bypass de rotas
  de PDF); manual de usuário por papel com os 17 estados reais.

### Pendências conhecidas (pré-produção)

Benchmark de anonimização com corpus real · `VISION_MODEL` no ambiente para
OCR externo de anexos · revisão humana dos 29 prompts · validação do
qcluster/workers em ambiente real · serviço web de produção com gunicorn
(hoje o Dockerfile é dev/runserver) · decisão de produto: negativa total na
aba "decididos" do doctor · texto informativo do parâmetro K.
Detalhes em `PROJECT_CONTEXT.md`.
