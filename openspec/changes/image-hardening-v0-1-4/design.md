# Design — image-hardening-v0-1-4

## D1 — Usuário não-root: uid fixo 10001, mídia com ownership na imagem

`groupadd -g 10001 hmd && useradd -u 10001 -g hmd -M -s /usr/sbin/nologin hmd`
+ `RUN mkdir -p /app/media && chown hmd:hmd /app/media` + `USER 10001:10001`
antes do `EXPOSE`/`CMD`. Justificativas:

- **uid/gid fixos e altos**: determinismo entre builds/hosts; sem colisão com
  uids do sistema da imagem base.
- **`/app/media` criado na imagem com ownership do usuário**: volume nomeado
  vazio inicializa a partir do mountpoint da imagem (ownership incluído) — a
  fase 2 (uploads ligados) nasce gravável, sem init container.
- **Sem chown recursivo de `/app`**: venv/estáticos/código foram criados como
  root com umask 022 → mundo-legível, suficiente para o processo ler; rootfs
  segue read-only em runtime (o processo não precisa escrever em /app).
- **Nada na fase 1 exige root**: gunicorn binda 8000 (>1024); secrets são
  montados legíveis; `/tmp` é tmpfs 1777; cache/sessão no banco.
- `-s /usr/sbin/nologin`: usuário de serviço, sem shell.

## D2 — WSGI fail-safe: default PROD

`os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")` — a
env explícita continua vencendo (compose/CI seguem idênticos); ausência de env
passa a cair na configuração MAIS restritiva. `manage.py` NÃO muda (default
dev preservado — CLI de desenvolvimento continua ergonômica; o perigo era
especificamente o entrypoint WSGI da imagem).

## D3 — Validação da imagem é do parent

O worker valida o que dá no repositório (regressões de settings + checks
estáticos do Dockerfile no padrão do `test_prod_secrets`). O build real +
smoke (boot do gunicorn como uid 10001 + `/healthz` 200 + `manage.py check`
com settings de prod dentro do container) é executado pelo parent após o
slice — mesmos moldes do piloto (workers não constroem imagem).

## D4 — Release v0.1.4 mecânica conhecida

Mesma cadência do v0.1.3: CHANGELOG `[0.1.4]`, bump `pyproject`/`uv.lock`,
default `${HMD_IMAGE_TAG:-v0.1.4}` no compose, pins `v0.1.4@sha256:<digest>`
no README/.env.example. `latest=false` já garantido no workflow (congelado).
Tag anotada + push + digest do GHCR **somente com autorização do dono**,
após o gate final.

## D5 — Spec

Duas MODIFIED em `production-deployment` (cenários existentes preservados na
íntegra + um cenário novo cada): "Runtime web…" ganha o usuário não-root;
"Configuração de produção fail-closed…" ganha o default do WSGI.
