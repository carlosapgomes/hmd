# Slice 003 — Release v0.1.4: bump, CHANGELOG, compose default, pins

## Objetivo

Mecânica de release da v0.1.4 (padrão v0.1.3): version bump, entrada de
CHANGELOG com os hardenings (não-root + WSGI fail-safe) e o carregamento dos
changes pós-piloto (guard por conjunto de papéis, labels de unidade
configuráveis), default `HMD_IMAGE_TAG` e pins de documentação atualizados.
Tag/push/publicação GHCR ficam owner-gated (tasks.md pós-gate).

## Contexto necessário (ler antes de editar)

- `CHANGELOG.md` — entrada `[0.1.3]` como molde (data de hoje: 2026-09-13).
- `pyproject.toml` (version) e `uv.lock` (bump via `uv lock`).
- `docker-compose.prod.yml` — `${HMD_IMAGE_TAG:-v0.1.3}` (6 ocorrências).
- `README.md` e `.env.example` — pins `v0.1.3@sha256:<digest>` e defaults.
- Git log recente para descrever com precisão o que entrou desde v0.1.3:
  `git log --oneline v0.1.3..HEAD`.

## Requisitos verificáveis

- **R1** — `pyproject.toml` e `uv.lock` em `0.1.4` (`uv lock` após editar).
- **R2** — `CHANGELOG.md` com `[0.1.4] — 2026-09-13` descrevendo: imagem
  não-root (uid 10001, mídia com ownership), WSGI default prod (fail-safe),
  guard de intranet por conjunto de papéis (change
  `intranet-any-role-egress`), labels de unidade configuráveis (change
  `unit-labels-env`), comentário de não-root do compose quitado.
- **R3** — Compose: todas as ocorrências de default de tag → `v0.1.4`;
  README e `.env.example` com pins `v0.1.4@sha256:<digest>` (digest é
  preenchido pelo owner na publicação — placeholders seguem o padrão atual).
- **R4** — Teste de regressão (se já existir assert de default de tag em
  `test_prod_secrets.py`, atualize-o para v0.1.4; se não existir, NÃO crie —
  este slice é documental).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `pyproject.toml`, `uv.lock` | `grep -n 'version = "0.1.4"' pyproject.toml uv.lock` |
| R2 | `CHANGELOG.md` | leitura; data e itens corretos |
| R3 | `docker-compose.prod.yml`, `README.md`, `.env.example` | `grep -rn "v0.1.3" docker-compose.prod.yml README.md .env.example` → 0 defaults/pins ativos (referências históricas do CHANGELOG não contam) |
| R4 | `apps/accounts/tests/test_prod_secrets.py` (só se houver assert) | suíte do arquivo verde |

## Escopo e expected blast radius

```yaml
expected_files:
  - CHANGELOG.md
  - pyproject.toml
  - uv.lock
  - docker-compose.prod.yml
  - README.md
  - .env.example
  - apps/accounts/tests/test_prod_secrets.py   # somente se existir assert de tag default

allowed_incidental_files: []

out_of_scope:
  - Dockerfile/wsgi (slices 001/002 encerrados)
  - workflow do GitHub (já tem latest=false; nada muda)
  - tag git/push/publicação (owner-gated)
```

## Plano de testes

### RED

- Sem RED comportamental (slice documental). Gate mínimo antes: `grep -rn "v0.1.3" docker-compose.prod.yml README.md .env.example` mostra os alvos a atualizar (evidência do estado atual).

### GREEN / verificação do slice

- `grep -n 'version = "0.1.4"' pyproject.toml uv.lock` — 2 matches
- `grep -rn "v0.1.3" docker-compose.prod.yml README.md .env.example` — 0 matches
- `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_prod_secrets.py -q` — verde
- `docker compose -f docker-compose.prod.yml --env-file .env.example config --quiet` (dummies de secret `_FILE` em /tmp se exigidas) — OK

## Critérios de aceitação

- [ ] R1–R4 verdes conforme a matriz
- [ ] CHANGELOG fiel ao que entrou (sem prometer o que não foi feito)
- [ ] Nenhum arquivo fora do blast radius
