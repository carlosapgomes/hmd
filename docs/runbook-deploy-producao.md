# Runbook de deploy em produção — HMD (servidor de produção)

Fonte única de verdade operacional, unificando: README (seção Piloto/fase 2),
spec `openspec/specs/production-deployment/spec.md` e o **estado real
verificado no servidor em 2026-11-14**. Em caso de conflito, este documento
prevalece; divergências conhecidas estão na seção final.

## 1. Topologia real (verificada no servidor de produção)

Host de produção, diretório do deploy: **`<dir-deploy>`** (clone git do repo —
serve de fonte do `docker-compose.prod.yml` e documentação; o código que roda
vem **sempre da imagem GHCR**, nunca do working tree).

| Elemento | Valor real |
|---|---|
| Projeto compose | `name: hmd-prod` (containers `hmd-prod-*`) |
| Serviços no ar | `web` (gunicorn, healthy em `/readyz/`) + workers `pdf`/`anonymization`/`llm`/`attachments` (django-q2) |
| Fase | **2 ATIVA** desde ~2026-09-26: workers no ar, `INTAKE_ENABLED=true` no web |
| Imagem | `ghcr.io/carlosapgomes/hmd:${HMD_IMAGE_TAG}` — **sempre pinada tag@digest** no `.env` |
| Ingress | rede `hospital_ingress_hmd`: Caddy externo → **web em IP fixo**
  (valor real só no host; ver seção 6.1), alias `hmd` |
| Banco | rede `hospital-db-hmd`: PostgreSQL compartilhado (container externo a
  este repo), DB `app_hmd`, host `postgres-app-hmd`, roles separadas
  `app_hmd_runtime` (DML + `hmd_cache`) e `app_hmd_migrator` (DDL, só no passo
  migrate) — nomes de usuário/roles são exemplos do piloto; os reais vivem no
  `.env` do host |
| Egress | rede `hospital_egress_hmd`: web sai só AD/DNS/Kerberos; workers também OpenRouter/OCR |
| Segredos | `./secrets/*.txt` (5 arquivos, owner `10001:10001`, modo `0600`): `secret_key`, `app_db_password`, `migrator_db_password`, `superuser_password`, `openrouter_api_key` |
| Volume | `hmd-prod_media_data` (uploads/anexos) |
| env-file | `--env-file .env` explícito em todo comando (não confiar no auto-load) |

Limites de memória (defaults do compose, sobrescrevíveis por
`WORKER_*_MEM_LIMIT`): pdf 512m · anonymization 2560m (2 processos spaCy) ·
llm 512m · attachments 512m.

## 2. Pré-requisitos (uma vez por host)

1. `docker login ghcr.io` com PAT `read:packages`.
2. Redes externas existindo: `hospital_ingress_hmd`, `hospital-db-hmd`,
   `hospital_egress_hmd` (fora deste repo).
3. PostgreSQL compartilhado com DB `app_hmd` + aliases `postgres-app-hmd`/`hmd`
   e as duas roles (runtime/migrator).
4. Caddy externo (fora deste repo, na rede de ingress) com upstream para o IP
   fixo do web.
5. NTP ativo (leases/work-lock dependem de relógio).
6. Segredos em `./secrets/` legíveis pelo uid/gid 10001.
7. `.env` no host: `HMD_IMAGE_TAG` pinado, `ALLOWED_HOSTS`,
   `CSRF_TRUSTED_ORIGINS`, `HMD_INGRESS_IPV4` (IP fixo do web na rede de
   ingress — ver 6.1), `PROXY_SSL_HEADER=true`,
   `TRUSTED_PROXY_HEADER=HTTP_CF_CONNECTING_IP`, `AD_DCS`,
   `INTRANET_IP_RANGE`, `DB_*`, `MIGRATOR_DB_USER`,
   `DJANGO_SUPERUSER_USERNAME`, `LLM1_MODEL`/`LLM2_MODEL`/`VISION_MODEL`.
   **`INTAKE_ENABLED` deve aparecer UMA única vez** (`true`) — ver divergências.

## 3. Upgrade de release (procedimento padrão)

Condições de entrada: workflow `release` verde no GHCR com o **digest da
imagem** impresso no passo final; suite verde; `CHANGELOG.md` lido (nota se há
migrations — o changelog marca "sem migrations (deploy direto)" quando é o caso).

```bash
cd /srv/apps/hmd

# 0. Backup do .env e do estado atual (antes de qualquer mudança):
cp .env ".env.bak-$(date -u +%Y%m%dT%H%M%SZ)"
docker compose --env-file .env -f docker-compose.prod.yml ps   # registrar o que está no ar

# 1. Atualizar o clone para a tag do release:
git fetch --tags --prune
git checkout --detach vA.B.C        # ex.: v0.1.16 — nunca branch, sempre tag

# 2. Compor o IP de ingress: a partir da release que parametrizou o compose
#    (v0.1.17+), o IP fixo vem da env — conferir no .env:
#      HMD_INGRESS_IPV4=<ip-do-web>      # dentro da subnet da rede de ingress
#    No upgrade TRANSICIONAL (último com patch local do ipv4_address),
#    reverter o patch do working tree (`git checkout -- docker-compose.prod.yml`)
#    e setar a env acima. `git diff` deve ficar VAZIO daí em diante.
#    Se o diff mostrar outra coisa: PARAR e reconciliar com o repo antes de seguir.

# 3. Conferir o pin no .env (uma linha só, tag@digest do workflow):
#    HMD_IMAGE_TAG=vA.B.C@sha256:<digest>
grep -n '^HMD_IMAGE_TAG' .env
grep -c '^INTAKE_ENABLED' .env      # deve imprimir 1

# 4. Puxar a imagem nova:
docker compose --env-file .env -f docker-compose.prod.yml pull

# 5. Migrações + seeds (one-shot; no-op em release "sem migrations"):
docker compose --env-file .env -f docker-compose.prod.yml --profile migrate run --rm migrate

# 6. Subir TUDO (web + workers) com a imagem nova:
docker compose --env-file .env -f docker-compose.prod.yml --profile workers up -d
```

### 3.1 Pós-checks (aceite do deploy)

```bash
docker compose --env-file .env -f docker-compose.prod.yml ps      # 5 serviços, web (healthy)
docker inspect --format '{{.State.Health.Status}}' hmd-prod-web-1 # healthy (não há curl na imagem)
docker exec hmd-prod-web-1 printenv INTAKE_ENABLED                # true
docker exec hmd-prod-worker-pdf-1 printenv INTAKE_ENABLED         # false
docker logs --tail 20 hmd-prod-web-1
```

Smoke funcional: abrir a URL pública do piloto (`<domínio público>`), login
AD, home por papel, e uma fila (visível no footer/manifest a versão do
release — `APP_DISPLAY_NAME`/ícones mudam quando o default do compose muda).

### 3.2 Housekeeping pós-aceite

```bash
docker image prune -f          # remove dangling <none> (não toca nas tagadas)
# manter a N-1 tagada para rollback; limpar N-2 em diante manualmente
```

Disco de referência: ~42% de 461G usados; cada imagem ~1GB.

## 4. Rollback

1. Repin a última imagem boa no `.env`
   (`v0.1.15@sha256:7b3f3ad0e55676e28d366faf72dcf0939992088516e4f5494040340b09dd30bf`
   enquanto a 0.1.15 for a N-1), `pull`, `--profile workers up -d`.
2. **Atenção a schema**: se a release nova aplicou migrations, avaliar
   reversibilidade antes do rollback de imagem (migrations HMD têm sido
   aditivas; conferir o changelog da release).
3. Kill-switch de intake (rollback de comportamento sem trocar imagem):
   `INTAKE_ENABLED=false` no `.env` + `up -d web` — o web volta a recusar
   envios; casos existentes permanecem íntegros.

## 5. Diagnósticos

```bash
# Modelos do pipeline (dentro do worker; valida OpenRouter + LLM1/LLM2):
docker compose --env-file .env -f docker-compose.prod.yml --profile workers \
  run --rm worker-llm python manage.py llm_check
# AD/Kerberos (dentro do web):
docker exec hmd-prod-web-1 python manage.py ad_check
# Logs:
docker logs --tail 50 hmd-prod-worker-llm-1   # idem web/pdf/anonymization/attachments
# Fila django-q2 (cluster) — saúde nos logs dos workers (qcluster broker/stats)
```

`VISION_MODEL` não tem comando de diagnóstico: conferir o valor no `.env`
(falha nomeada fail-closed no processamento do anexo, caso vazio/errado).

## 6. Divergências conhecidas (servidor × repo) e plano

### 6.1 IP fixo do ingress (parametrizado a partir da release com a env)

**Histórico:** o compose do servidor carregava um patch local
(`ipv4_address: <ip-do-web>` no web em `hospital_ingress_hmd`), pois o repo é
**público** e não versiona IPs internos do hospital. **Resolvido:** o compose
passou a exigir `HMD_INGRESS_IPV4` (env obrigatória, fail-closed, sem default)
— o valor vive só no `.env` do host, dentro da subnet da rede externa de
egress→ingress. No primeiro upgrade com a env, reverter o patch local
(`git checkout -- docker-compose.prod.yml`) e o `git status` do clone fica
limpo permanentemente.

### 6.2 `INTAKE_ENABLED` duplicado no `.env` (ativo)

O `.env` do host tem a chave duas vezes (`false` no meio, `true` no fim).
Hoje funciona — a última ocorrência vence para o web e os workers têm `false`
fixo no compose — mas é frágil: uma reordenação silenciosa mudaria o intake.
**Plano:** eliminar a ocorrência `false` do `.env` (deixar só `true`); o
passo 3 do upgrade já valida `grep -c` = 1.

### 6.3 README descreve a fase 1 como "atual" (docs)

A seção "Piloto (fase 1)" do README ainda narra os workers como desligados e a
fase 2 como checklist futuro. Em produção a fase 2 está **ativa**. Este
runbook registra o estado real; o README aponta para cá.

### 6.4 Clone defasado entre releases (operacional)

O clone em `/srv/apps/hmd` fica na tag do último deploy entre upgrades — é
esperado; ele existe para fornecer o compose e a documentação da versão
implantada. Não há `git pull` automático; o passo 1 do upgrade sincroniza.

## 7. Quick reference

| O quê | Comando |
|---|---|
| Estado geral | `docker compose --env-file .env -f docker-compose.prod.yml ps` |
| Health do web | `docker inspect --format '{{.State.Health.Status}}' hmd-prod-web-1` |
| Versão rodando | `docker inspect --format '{{.Config.Image}}' hmd-prod-web-1` |
| Digest no GHCR | `docker buildx imagetools inspect ghcr.io/carlosapgomes/hmd:<tag>` |
| Reiniciar só o web | `docker compose --env-file .env -f docker-compose.prod.yml up -d web` |
| Parar workers (rollback fase 2) | `docker compose --env-file .env -f docker-compose.prod.yml --profile workers stop` |
