# Design: pilot-deployment-v0-1-1

Requisitos fechados pelo owner (via sessão de infraestrutura, 2026-09-11):
auth real CPF+senha AD via AS-REQ (sem SPNEGO/service account/keytab — já é o
implementado); URL `https://hmd.projetoshgrs.com`; NIR restrito por
`INTRANET_IP_RANGE` (valor fora do Git — já é env); fase 1 sem upload
(operacional: workers desligados); cache = DatabaseCache (NÃO Redis), tabela
`hmd_cache` criada pelo migrator; secrets por arquivos, nunca values no Git;
`AD_DCS` fora do Git (já é); PG compartilhado externo, DB `app_hmd`, aliases
`postgres-app-hmd`/`hmd`, redes externas `hospital-db-hmd`/
`hospital_ingress_hmd`/`hospital_egress_hmd`; web sem porta no host; Caddy
upstream; migrate one-shot com credencial migrator; GHCR amd64, primeira
imagem v0.1.1.

## D1 — Runtime web (gunicorn + build)

- `gunicorn>=23,<24` entra em `dependencies` (deploy é parte do produto).
- Dockerfile: mantém o build de deps/modelo; adiciona
  `RUN DJANGO_SECRET_KEY=build-dummy DATABASE_URL=postgres://build:build@localhost/build python manage.py collectstatic --noinput --settings=config.settings.prod`
  (estáticos na imagem; nenhum secret real no build) e
  `CMD ["gunicorn","config.wsgi:application","--bind","0.0.0.0:8000","--workers","3","--threads","2","--timeout","60","--graceful-timeout","30","--capture-output","--access-logfile","-","--error-logfile","-"]`.
- Logs em stdout/stderr (rotação é do log driver do compose: `json-file`
  com `max-size`/`max-file` — D3).

## D2 — Health/readiness sem dados

- `GET /healthz` → 200 `{"status":"ok"}` (liveness; não toca DB; pública).
- `GET /readyz` → 200 `{"status":"ready"}` só se `SELECT 1` no DB default
  (readiness; sem dados de negócio); 503 `{"status":"unavailable"}` caso
  contrário. Ambas sem login e **isentas do IntranetGuard** ( adicionadas a
  `EXEMPT_PATHS`). Implementadas em `apps/accounts/views_health.py` (ou
  módulo novo `config`-adjacente — ver slice) com urls globais
  `healthz`/`readyz`; o healthcheck do compose web usa `/readyz`.

## D3 — Settings de produção (config/settings/prod.py)

- **Cache**: `CACHES = {"default": {"BACKEND":
  "django.core.cache.backends.db.DatabaseCache" (corrigido em v0.1.3: o path original ...backends.database. era inválido e falhava no createcachetable/runtime), "LOCATION":
  "hmd_cache"}}` (default de prod; o guard anti-LocMem permanece). A tabela é
  criada pelo **migrator** no perfil one-shot: `manage.py migrate && manage.py
  createcachetable hmd_cache` (comando idempotente do Django; sem migration
  de app — a tabela é de infraestrutura, não de domínio).
- **Hosts/CSRF/Proxy** (env-driven):
  - `ALLOWED_HOSTS` (já existe; default vazio) — `.env.example` documenta
    `hmd.projetoshgrs.com`.
  - `CSRF_TRUSTED_ORIGINS = [o.strip() for o in
    os.environ.get("CSRF_TRUSTED_ORIGINS",
    "https://hmd.projetoshgrs.com").split(",") if o.strip()]` (default =
    URL pública do piloto — DNS público, não secret).
  - `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` quando
    `PROXY_SSL_HEADER` env estiver ligado (default ligado no piloto — Caddy
    termina TLS e injeta `X-Forwarded-Proto`); `SECURE_PROXY_SSL_HEADER=None`
    se desligado.
- Nada de valores de secret em código: `DJANGO_SECRET_KEY` segue obrigatória
  por env; `DB_PASSWORD_FILE` já suportado pelo parsing existente de
  DATABASE_URL/DB_*.

## D4 — docker-compose.prod.yml (topologia do piloto)

```yaml
# Serviços:
# web:        imagem GHCR hmd; sem ports (expose 8000 pelo Dockerfile);
#             redes: hospital_ingress_hmd (Caddy) + hospital-db-hmd (PG) +
#             hospital_egress_hmd (AD/DNS/Kerberos — egress restrito);
#             healthcheck /readyz; logging json-file rotacionado; profile
#             default (ligado).
# migrate:    one-shot (profile "migrate"): web + command migrate &&
#             createcachetable hmd_cache && seed_admin && seed_prompts &&
#             seed_procedure_catalog; usa credencial de MIGRATOR (env
#             própria) — não a do app; restart: "no".
# worker-pdf|anonymization|llm|attachments: definidos sob profile
#             "workers" (DESLIGADO na fase 1); redes mínimas por função
#             (db + egress p/ llm/attachments; apenas db p/ pdf/anonymization
#             no piloto); sem ports.
# Rede/volumes: redes EXTERNAS hospital-db-hmd / hospital_ingress_hmd /
# hospital_egress_hmd (declaradas external: true — geridas fora do repo);
# aliases do serviço de PG pertencem ao compose externo (o HMD referencia
# pelo alias); volume media_data.
# Env/secrets: tudo por ${VAR} (arquivo .env no host ou secrets manager) —
# nenhum value no Git; DB app_hmd via DATABASE_URL/MIGRATOR_DATABASE_URL.
```

Validação local: `docker compose -f docker-compose.prod.yml config --quiet`
(usuário fornece envs dummy; o arquivo não pode conter values reais).

## D5 — Release GHCR (v0.1.1, amd64)

`.github/workflows/release.yml`: `on: push: tags: ["v*"]`;
`permissions: contents: read, packages: write`;
docker/login-action (ghcr, `${{ github.token }}`) → build-and-push-action
`linux/amd64` (only), push `ghcr.io/carlosapgomes/hmd:${tag}` e `:latest`.
CHANGELOG `[0.1.1]`, `pyproject` version `0.1.1`, README com seção "Piloto
(fase 1)" (topologia, migrate one-shot, workers desligados, egress, Caddy).
Tag anotada `v0.1.1` somente após review final; digest da imagem reportado.

## D6 — Guardrails

- Sem leitura/gravação de `.env`, secrets, media, logs ou containers.
- Compose/web não recebem `OPENROUTER_API_KEY`/`VISION_MODEL` na fase 1
  (egress LLM desligado por ausência de workers E de chave).
- `INTRANET_RESTRICTED_ROLES` segue default `nir` (igual ATS);
  `INTRANET_IP_RANGE` por env no host (fora do Git); NOTA (corrigida em 2026-09-11, finding do blueprint): na topologia REAL do piloto (Cloudflared → Caddy → HMD) o default `TRUSTED_PROXY_HEADER=HTTP_CF_CONNECTING_IP` é o CORRETO — a borda Cloudflare fixa esse header com o IP real do cliente e o Caddy o repassa sem tocar (NÃO usar `header_up X-Forwarded-For {remote_host}` nesse hop: gravaria o IP do cloudflared). XFF+header_up vale apenas em topologia Caddy-direto (a nota original da review 001 estava errada para esta topologia).

## D7 — Intake desligável fail-closed (decisão owner, fase 1)

- Setting `INTAKE_ENABLED` (env bool): base default **true** (dev/teste);
  `prod.py` default **false** (mesmo padrão fail-closed dos flags
  `*_RUN_TASKS_INLINE`).
- Boundary de SERVIÇO (`apps/intake/services.py`): guard `_assert_intake_enabled()`
  no TOPO de `create_case_with_documents`, `create_corrected_resubmission` e
  `resubmit_case_documents` — `ValueError` nomeado ("Intake desabilitado
  neste ambiente…") ANTES de qualquer validação/escrita/arquivo/enqueue
  (cobertura para qualquer caller, não só HTTP).
- Views (`intake:home`, `intake:case_resubmit`, `intake:gate_resubmit`): no
  POST, se desligado → flash informativo + redirect (200-friendly, sem
  traceback); GET segue renderizando (nada de ocultar/ampliar UI).
- Testes: POST bloqueado ⇒ 0 rows de Case/CaseDocument/CaseAttachment, 0
  enqueue (com `INTAKE_RUN_TASKS_INLINE=False` + assert de não-enfileiramento
  — padrão da suíte do 04); serviço direto levanta erro nomeado sem efeito;
  dev/teste (default true) segue criando (regressão).
- Spec delta: `production-deployment` ADDED "Intake desabilitável
  fail-closed" (cenários de POST bloqueado e serviço fail-closed).
