# Slice 002 — Imagem não-root: usuário dedicado 10001 + mídia com ownership

## Objetivo

A imagem de produção passa a executar como usuário dedicado **não-root**
(uid/gid 10001, sem shell), com `/app/media` criado na imagem e pertencente
ao usuário (volumes nomeados herdam o ownership na primeira montagem — fase 2
nasce gravável). Nada em runtime exige root.

## Contexto necessário (ler antes de editar)

- `Dockerfile` — inteiro (~50 linhas): sequência atual (uv sync → COPY →
  collectstatic com envs dummy → EXPOSE → CMD gunicorn).
- `docker-compose.prod.yml:45-55` — comentário do risco root ("non-root fica
  p/ o próximo release (v0.1.4+)") e mitigações (read_only, tmpfs 1777,
  cap_drop ALL, no-new-privileges) — o comentário do adiamento sai; as
  mitigações FICAM (defesa em profundidade).
- `docker-compose.prod.yml:120-121` — volume `media_data:/app/media` (web e
  workers): o mountpoint na imagem precisa existir com ownership do usuário.
- `apps/accounts/tests/test_prod_secrets.py` — padrão dos checks estáticos
  de artefatos de deploy (asserts de conteúdo de arquivo; ex.:
  `test_compose_repasses_secret_files`); é o lar das regressões do Dockerfile.
- `openspec/changes/image-hardening-v0-1-4/design.md` — D1 (uid fixo, sem
  chown recursivo, nologin, por que nada exige root) e D3 (build/smoke é do
  parent).
- Cenário novo da spec: "Processo web roda como não-root".

## Requisitos verificáveis

- **R1** — `Dockerfile` cria grupo/usuário `hmd` com uid/gid **10001**,
  `-M -s /usr/sbin/nologin`, cria `/app/media` com `chown hmd:hmd` e declara
  `USER 10001:10001` antes do `EXPOSE`; nenhuma instrução posterior re-eleva
  root.
- **R2** — Regressões estáticas em `test_prod_secrets.py` (padrão dos checks
  de compose): (a) Dockerfile contém `USER 10001:10001` APÓS a última linha
  `RUN`/`COPY` que escreve em `/app` (ordem: useradd → media → collectstatic
  continua como root no BUILD — coleta antes do USER está ok; o processo de
  runtime é o não-root) — assert simples: existe `USER 10001:10001` e não
  existe `USER root`/`USER 0`; (b) existe `chown hmd:hmd /app/media`; (c) o
  comentário "non-root fica p/ o próximo release" SUMIU do compose (a dívida
  foi paga), mantendo read_only/cap_drop/no-new-privileges (assert destas
  três mantidas).
- **R3** — Estáticos coletados no build continuam funcionando como não-root:
  a camada `collectstatic` fica ANTES do `USER` (build roda como root; runtime
  lê) — assert de ordem: linha de `collectstatic` precede `USER`.
- **R4** — Healthcheck do compose (python urllib) e serviços one-shot
  (migrate/seeds) seguem viáveis como uid 10001: nenhum bind <1024, nenhuma
  escrita fora de /tmp e /app/media (verificado por leitura; smoke real é do
  parent, design D3).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `Dockerfile` | `test_dockerfile_runs_as_non_root` (estático: USER 10001:10001, sem USER root/0, useradd/groupadd 10001, chown media) |
| R2 | `docker-compose.prod.yml`, `test_prod_secrets.py` | `test_dockerfile_nonroot_regression` + assert compose: mitigações mantidas, comentário de dívida removido |
| R3 | `Dockerfile` | assert de ordem collectstatic < USER (no teste de R1) |
| R4 | — (leitura) | smoke não-root executado pelo PARENT (design D3): build + boot gunicorn uid 10001 + `/healthz` 200 + `manage.py check` prod no container |

## Escopo e expected blast radius

```yaml
expected_files:
  - Dockerfile
  - docker-compose.prod.yml        # apenas o comentário do adiamento (nenhuma mudança funcional)
  - apps/accounts/tests/test_prod_secrets.py
  # DESVIO AUTORIZADO pelo dono (review rodada 1, P1; opção A): o USER 10001
  # quebrava o fluxo dev (uv sync em venv root-owned + cache + media).
  # docker-compose.dev.yml entrou NO FIX CYCLE com `user: "0:0"` deliberado
  # nos 5 serviços de imagem (dev/teste-server root; prod segue non-root) +
  # teste estático travando (test_dev_compose_runs_as_root_deliberately).
  - docker-compose.dev.yml

allowed_incidental_files: []

out_of_scope:
  - config/wsgi.py (slice 001), release/bump/CHANGELOG (slice 003)
  - settings, migrations, código Python de aplicação
  - compose: qualquer mudança funcional (usuário, volumes, permissões de montagem)
```

Escalamento: se o build real (parent) falhar por permissão que exija mudar o
compose ou adicionar chown recursivo, **pare e reporte** — não amplie.

## Notas de implementação

- Bloco sugerido (após o collectstatic, antes do EXPOSE):
  `RUN groupadd -g 10001 hmd && useradd -u 10001 -g hmd -M -s /usr/sbin/nologin hmd && mkdir -p /app/media && chown hmd:hmd /app/media`
  seguido de `USER 10001:10001`.
- NÃO adicione `chown -R` de `/app` (desnecessário e destrói a camada de
  cache); umask 022 do build deixa venv/estáticos mundo-legíveis.
- Atualize o comentário do compose para refletir o estado novo (não-root
  ATIVO + mitigações mantidas por defesa em profundidade).

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_prod_secrets.py -q`
- falha esperada: os testes novos de Dockerfile falham (não existe `USER
  10001:10001` nem `chown hmd:hmd /app/media` no Dockerfile atual).

### GREEN

- mesmo comando — 0 failed.

### Verificação do slice

- `TEST_DB_PORT=55435 uv run pytest apps/accounts -q` — 0 failed
- `APP_DB_PASSWORD_FILE=… MIGRATOR_DB_PASSWORD_FILE=… SECRET_KEY_FILE=… SUPERUSER_PASSWORD_FILE=… docker compose --profile migrate --profile workers -f docker-compose.prod.yml --env-file .env.example config --quiet` — OK (dummies em /tmp)
- `uv run ruff check apps/accounts/tests/test_prod_secrets.py && uv run ruff format --check apps/accounts/tests/test_prod_secrets.py` — ok
- `uv run mypy apps` — ok
- **Parent (design D3)**: `docker build` + smoke não-root (id do processo =
  10001, `/healthz` 200, `manage.py check --settings=config.settings.prod`
  com envs dummy no container).

## Critérios de aceitação

- [ ] R1–R4 verdes conforme a matriz (R4 = smoke do parent)
- [ ] RED demonstrado antes do GREEN (mesmo comando)
- [ ] Compose sem mudança funcional; nenhuma re-elevação de root no Dockerfile
