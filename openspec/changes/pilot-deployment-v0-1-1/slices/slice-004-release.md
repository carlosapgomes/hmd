# Slice 004: Workflow GHCR + CHANGELOG v0.1.1 + bump

## Objetivo

Pipeline de release: em tag `v*`, build `linux/amd64` da imagem e push para
`ghcr.io/carlosapgomes/hmd` (tag da versão + latest), com metadata de digest
no run; CHANGELOG `[0.1.1]`; `pyproject` version 0.1.1.

## Contexto necessário

- Design D5 (`openspec/changes/pilot-deployment-v0-1-1/design.md`).
- Actions padrões: `docker/setup-buildx-action`, `docker/login-action`
  (ghcr com `${{ github.token }}`), `docker/metadata-action`,
  `docker/build-push-action` (platforms `linux/amd64`, push, provenance
  false ou true — amd64 único), `permissions: {contents: read, packages:
  write}`.
- `.dockerignore` já exclui .env*/temp/media/.git (verificar — build context
  limpo).
- CHANGELOG.md existente (seção [0.1.0] como modelo).

## Requisitos verificáveis

- **R1** `.github/workflows/release.yml`: `on: push: tags: ["v*"]`;
  checkout; login ghcr; build-push amd64 com tags
  `ghcr.io/carlosapgomes/hmd:${{ github.ref_name }}` e `:latest`;
  `permissions` mínimos; passo final imprime o digest (para o reporte).
- **R2** CHANGELOG: `[0.1.1] — <data>` descrevendo: runtime gunicorn +
  healthchecks, DatabaseCache hmd_cache, hardening CSRF/proxy, compose
  produtivo do piloto (fase 1: intake desligado, workers/egress LLM
  desligados), imagem GHCR amd64; nota de ativação futura
  (workers/egress/INTAKE_ENABLED juntos na próxima mudança).
- **R3** `pyproject.toml` version `0.1.1`.
- **R4** Sem segredos no workflow (só `${{ github.token }}`); sem
  `pull_request` trigger; tag v0.1.1 NÃO é criada aqui (só após review
  final — gate 4.1).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) | Teste/check |
| --- | --- | --- |
| R1 | `.github/workflows/release.yml` | `actionlint` se disponível; revisão |
| R2 | `CHANGELOG.md` | revisão |
| R3 | `pyproject.toml` | grep version |
| R4 | workflow | grep sem secrets |

## Escopo e expected blast radius

```yaml
expected_files:
  - .github/workflows/release.yml
  - CHANGELOG.md
  - pyproject.toml

out_of_scope:
  - push da tag (gate 4.1, após review); multi-arch; snapshot builds em PR
```

## Plano de testes do slice

### RED

- `ls .github/workflows/release.yml` → inexistente.

### GREEN / verificação local

- YAML válido (`python -c "import yaml,sys; yaml.safe_load(open(...))"`);
  `TEST_DB_PORT=55435 uv run pytest -q` (regressão).

## Critérios de aceitação

- [ ] R1–R4; workflow mínimo e sem segredos
- [ ] CHANGELOG/versão coerentes com o release v0.1.1
- [ ] Tag NÃO criada neste slice
