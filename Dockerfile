# syntax=docker/dockerfile:1

# Imagem mínima para o serviço web do compose de desenvolvimento (slice 002).
# O código-fonte e o pyproject/uv.lock chegam por bind-mount em runtime
# (docker-compose.dev.yml), que sincroniza as dependências no volume app_venv
# com `uv sync --frozen` antes de subir o runserver.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

EXPOSE 8000
