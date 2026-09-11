# Proposal: pilot-deployment-v0-1-1

## Problema

O release v0.1.0 é deployável apenas pelo caminho de desenvolvimento
(runserver + compose dev). O piloto hospitalar (fase 1: login/admin/navegação,
sem upload; workers e egress LLM desligados) exige um runtime de produção:
imagem com gunicorn e estáticos coletados, healthchecks, cache compartilhado
sem Redis (DatabaseCache), hardening de proxy/TLS, compose produtivo sobre
PostgreSQL compartilhado com redes externas, e pipeline de release público
(GHCR, amd64).

## Objetivo

1. **Runtime web de produção**: `gunicorn` como dependência + `CMD` no
   Dockerfile; `collectstatic` no build; endpoints públicos sem dados
   `/healthz` (liveness) e `/readyz` (readiness com ping de DB); logs em
   stdout com rotação pelo log driver do compose.
2. **Settings de produção**: `CACHES` = `DatabaseCache` tabela **`hmd_cache`**
   (criada pelo migrator via `createcachetable`, idempotente — guard
   fail-closed contra LocMem mantido); `ALLOWED_HOSTS` (env, default
   `hmd.projetoshgrs.com`), `CSRF_TRUSTED_ORIGINS` e
   `SECURE_PROXY_SSL_HEADER` (env-driven, proxy Caddy à frente).
3. **Compose produtivo** (`docker-compose.prod.yml`): web sem porta publicada
   (expose 8000; Caddy é upstream na rede `hospital_ingress_hmd`); PostgreSQL
   17/18 compartilhado **externo** pela rede `hospital-db-hmd` (DB `app_hmd`,
   aliases `postgres-app-hmd` e `hmd`); perfil `migrate` one-shot com credencial
   de migrator; 4 workers definidos sob perfil DESLIGADO com redes mínimas;
   egress da web apenas AD/DNS/Kerberos (rede `hospital_egress_hmd` para o que
   precisar sair); secrets por arquivos/env, nunca valores no Git.
4. **Pipeline de release**: workflow GitHub Actions `release.yml` — em tag
   `v*`, build **amd64** e push para `ghcr.io/carlosapgomes/hmd` (tag da
   versão + `latest`); primeira imagem implantável **v0.1.1**; CHANGELOG e
   README (seção piloto) atualizados.
5. **Intake desligável fail-closed** (decisão do owner, fase 1 "nenhum
   relatório"): setting env `INTAKE_ENABLED` — default **false em produção**,
   true em dev/teste; bloqueio nos boundaries de serviço
   (`create_case_with_documents`, `create_corrected_resubmission`,
   `resubmit_case_documents`) com erro nomeado ANTES de qualquer
   escrita/arquivo, e as rotas de POST informam o usuário sem efeito; testes
   provam que nada (caso/documento/anexo/tarefa) é criado/enfileirado.
   Ativação futura fica registrada junto aos workers/egress na próxima
   mudança.

## Escopo

**Inclui**: gunicorn (dep), Dockerfile (CMD + collectstatic), healthz/readyz
(views/urls/tests), settings prod (cache DB + hosts/CSRF/proxy),
`docker-compose.prod.yml`, `.env.example` (nomes novos),
`.github/workflows/release.yml`, CHANGELOG v0.1.1, README piloto, bump
pyproject 0.1.1.

**Não inclui** (fase 1, fechado pelo owner): Redis; SPNEGO/keytab; egress
OpenRouter (workers desligados e sem chave); ocultar dados existentes ou
ampliar UI do intake (bloqueio é na mutação); migração de dados; Caddy em si
(externo, upstream); ativação de workers/egress/intake (próxima mudança).

## Sucesso

- `docker compose -f docker-compose.prod.yml config --quiet` ok; `up` do web
  responde `/healthz` 200 sem auth e `/readyz` 200 com DB no ar; login/admin
  navegáveis; cache anti-lockout na tabela `hmd_cache`.
- Workflow em tag `v0.1.1` publica imagem amd64 no GHCR com digest reportado.
- Suíte completa verde + ruff/format/mypy (gates do AGENTS.md).

## Capabilities

- `production-deployment` (nova) — `specs/production-deployment/spec.md`.
