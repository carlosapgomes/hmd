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

# Workers executam o venv da imagem diretamente (imagem pronta, sem uv run
# resolvendo sync em runtime).
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
