# Slice 003: docker-compose.prod.yml — topologia do piloto

## Objetivo

Compose produtivo da fase 1: web (gunicorn, sem porta no host, healthcheck
/readyz, logs rotacionados), serviço `migrate` one-shot (profile próprio,
credencial de migrator, cria `hmd_cache` + seeds), 4 workers DEFINIDOS sob
profile desligado com redes mínimas, PostgreSQL compartilhado EXTERNO (DB
`app_hmd`, aliases `postgres-app-hmd`/`hmd`), redes externas
`hospital-db-hmd`/`hospital_ingress_hmd`/`hospital_egress_hmd`, secrets
apenas por `${VAR}` (nunca values no Git).

## Contexto necessário

- Design D4 (`openspec/changes/pilot-deployment-v0-1-1/design.md`).
- `docker-compose.dev.yml` (padrão de workers/Q_CLUSTER_NAME/flags; NÃO
  copiar o runserver/bind — prod usa a imagem GHCR pronta e o CMD do
  Dockerfile).
- `config/settings/prod.py` (envs exigidos: DJANGO_SECRET_KEY,
  DATABASE_URL, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, PROXY_SSL_HEADER,
  INTAKE_ENABLED, AD_DCS…).
- Sintaxe compose: `profiles: ["nome"]` desliga serviço por padrão;
  `networks: <nome>: {external: true}`; `expose` vs `ports`;
  `healthcheck` com `CMD` curl/python; `logging: {driver: json-file,
  options: {max-size, max-file}}`; aliases de rede pertencem ao compose
  EXTERNO que declara o PG (o HMD só referencia pelo alias/host).

## Requisitos verificáveis

- **R1** `docker-compose.prod.yml`: serviço `web` (image
  `ghcr.io/carlosapgomes/hmd:${HMD_IMAGE_TAG:-v0.1.1}`, SEM `ports`,
  `expose: ["8000"]`; redes hospital_ingress_hmd + hospital-db-hmd +
  hospital_egress_hmd; healthcheck `GET /readyz`; logging json-file
  rotacionado (max-size 10m, max-file 3); envs por `${VAR}`; depende do
  migrate apenas documentacional — sem depends_on bloqueante).
- **R2** Serviço `migrate` (profile `migrate`, `restart: "no"`): command
  `sh -c "python manage.py migrate --settings=config.settings.prod &&
  python manage.py createcachetable hmd_cache --settings=config.settings.prod &&
  python manage.py seed_admin --settings=config.settings.prod &&
  python manage.py seed_prompts --settings=config.settings.prod &&
  python manage.py seed_procedure_catalog --settings=config.settings.prod"`;
  usa `MIGRATOR_DATABASE_URL` (credencial própria); redes hospital-db-hmd.
- **R3** Workers `worker-pdf|anonymization|llm|attachments` sob profile
  `workers` (desligado): redes mínimas — pdf/anonymization: hospital-db-hmd
  (anonymization + hospital_egress_hmd? NÃO — anonimização não sai; só db);
  llm/attachments: hospital-db-hmd + hospital_egress_hmd (OpenRouter quando
  ativos); mesmos Q_CLUSTER_NAME/flags do dev compose; sem ports; sem
  OPENROUTER/VISION envs na fase 1 (comentário: ativam junto ao profile).
- **R4** Redes externas declaradas `external: true` (hospital-db-hmd,
  hospital_ingress_hmd, hospital_egress_hmd); volume `media_data`; DB
  `app_hmd` referenciado pelas DATABASE_URL (valor fora do Git).
- **R5** `.env.example`: NOVOS nomes documentados (HMD_IMAGE_TAG,
  MIGRATOR_DATABASE_URL, CSRF_TRUSTED_ORIGINS, PROXY_SSL_HEADER,
  INTAKE_ENABLED) SEM values reais; README: seção "Piloto (fase 1)" —
  topologia, migrate one-shot, workers desligados, egress só AD/DNS p/ web,
  Caddy upstream, sem porta no host.
- **R6** Validação: `docker compose -f docker-compose.prod.yml --env-file
  .env.example config --quiet` passa (ou com envs dummy em shell); nenhum
  segredo literal no yml.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) | Teste/check |
| --- | --- | --- |
| R1–R4 | `docker-compose.prod.yml` | `compose config --quiet`; revisão por leitura |
| R5 | `.env.example`, `README.md` | revisão + grep sem values |
| R6 | — | comando documentado no relatório |

## Escopo e expected blast radius

```yaml
expected_files:
  - docker-compose.prod.yml
  - .env.example
  - README.md

out_of_scope:
  - Caddy/PG externos (geridos fora do repo); workflow (004); código (001/002)
```

## Plano de testes do slice

### RED

- `docker compose -f docker-compose.prod.yml config --quiet` → arquivo
  inexistente (exit ≠ 0).

### GREEN / verificação local

- `docker compose -f docker-compose.prod.yml --env-file .env.example config --quiet`
- `TEST_DB_PORT=55435 uv run pytest -q` (regressão — nenhum código mudou)

## Critérios de aceitação

- [ ] R1–R6; web sem porta publicada; migrate cria hmd_cache + seeds
- [ ] Workers sob profile desligado com redes mínimas por função
- [ ] Zero segredos literais; apenas ${VAR}
- [ ] compose config --quiet verde
