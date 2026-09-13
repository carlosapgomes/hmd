# syntax=docker/dockerfile:1

# Imagem de runtime do HMD com dependências + modelo spaCy instalados no BUILD
# (slice 004, R5, design D7): os workers (worker-pdf/worker-anonymization)
# usam a imagem pronta — sem `uv sync` no startup, sem bind do código. O web
# dev mantém o fluxo de volume/runserver de sempre (bind do código + volume
# app_venv sincronizados em runtime no docker-compose.dev.yml).
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependências + modelo pt_core_news_lg (~541 MB) no BUILD: o lock versionado
# garante a instalação determinística; a camada fica em cache enquanto o lock
# não muda (o grupo dev de pytest/mypy/ruff não entra na imagem — --no-dev).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Código-fonte por último (as camadas de deps/modelo são reusadas entre
# builds). O .dockerignore mantém fora do contexto .venv/caches/git do host —
# o /app/.venv recém-instalado acima nunca é sobrescrito pelo COPY.
COPY . /app

# Workers/collectstatic executam o venv da imagem diretamente (imagem pronta,
# sem uv run resolvendo sync em runtime).
ENV PATH="/app/.venv/bin:$PATH"

# Estáticos coletados no BUILD (change pilot-deployment-v0-1-1, slice 001/R2,
# design D1): o WhiteNoise de produção
# (CompressedManifestStaticFilesStorage) exige os arquivos + manifest dentro da
# imagem, e o .dockerignore mantém ``staticfiles/`` fora do contexto — eles
# entram por esta camada. As envs são DUMMY (o import de prod exige secret e
# banco válidos; o collectstatic não abre conexão) — nenhum secret real no
# build.
RUN DJANGO_SECRET_KEY=build-dummy \
    DATABASE_URL=postgres://build:build@localhost/build \
    python manage.py collectstatic --noinput --settings=config.settings.prod

# Usuário dedicado não-root (change image-hardening-v0-1-4, slice 002/R1,
# design D1): uid/gid fixos e altos (determinismo entre builds/hosts, sem
# colisão com os uids da imagem base) e sem shell. O `/app/media` nasce na
# imagem com o ownership do usuário para que o volume nomeado vazio (web e
# workers) o herde na primeira montagem — a fase 2 sobe gravável sem init
# container. O BUILD acima seguiu como root (o umask 022 deixa venv/estáticos
# mundo-legíveis) e o runtime é read-only: nada exige root (gunicorn em 8000
# >1024, secrets montados legíveis, `/tmp` em tmpfs 1777, estado no banco).
# Sem `chown -R` de `/app`: a camada de cache fica intacta e o processo só
# precisa LER o código.
RUN groupadd -g 10001 hmd \
    && useradd -u 10001 -g hmd -M -s /usr/sbin/nologin hmd \
    && mkdir -p /app/media \
    && chown hmd:hmd /app/media

# Daqui para baixo tudo executa como o uid/gid 10001 (nenhuma re-elevação).
USER 10001:10001

EXPOSE 8000

# Servidor de produção (R2/design D1): logs em stdout/stderr (o log driver do
# compose rotaciona), 3 workers x 2 threads para o perfil do piloto.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "2", "--timeout", "60", "--graceful-timeout", "30", "--capture-output", "--access-logfile", "-", "--error-logfile", "-"]
