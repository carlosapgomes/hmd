# Slice 001 — Segredo OpenRouter por arquivo + modelos + limites nos workers

## Contexto necessário

- Padrão fail-closed de segredo-por-arquivo já existe INLINE em
  `config/settings/db.py` (procure a função que lê `Path(...).read_text()...
  .strip()` com `ImproperlyConfigured`): extrair para
  `config/settings/_secrets.py` e reusar NOS DOIS lugares — semântica
  idêntica (arquivo vence; ilegível/vazio → `ImproperlyConfigured`).
- Settings em `config/settings/base.py` (~333-347): `OPENROUTER_API_KEY`
  hoje é `os.environ.get("OPENROUTER_API_KEY", "")` — trocar pelo helper
  (D1). `LLM1_MODEL`/`LLM2_MODEL`/`VISION_MODEL`/`OPENROUTER_BASE_URL`/
  `LLM_TIMEOUT_SECONDS` permanecem como estão.
- Compose `docker-compose.prod.yml`: `worker-llm` e `worker-attachments`
  já estão na rede `hospital_egress_hmd`; `worker-pdf`/`worker-anonymization`
  NÃO têm egress. Secrets top-level em `secrets:` (~348). O comentário do
  bloco workers (~203) diz que as envs "NÃO entram na fase 1" — atualizar.
- Guards de compose em `apps/accounts/tests/test_prod_secrets.py`
  (COMPOSE_FILE, `_deploy_artifact`, resolução com TODOS os profiles, ban de
  `user:` em :625, secrets por consumidor).
- `INTAKE_ENABLED` do web PERMANECE `${INTAKE_ENABLED:-false}` (D4).

## Goal

Workers prontos para a fase 2: chave só por arquivo nos 2 workers com egress,
modelos passáveis, limites de memória nos 4; nada ativa por si só.

## Deliverables

### R1 — Helper compartilhado (`config/settings/_secrets.py`)

- Função pura `secret_from_env(env, *, secret_file_key, env_key, setting_name)`
  com a semântica exata do padrão atual; `db.py` refatorado para usá-la
  (comportamento idêntico — os testes existentes de precedência/fail-closed
  em `test_prod_secrets.py` DEVEM continuar verdes sem edição).

### R2 — `OPENROUTER_API_KEY_FILE` no settings (base.py)

- `OPENROUTER_API_KEY = secret_from_env(...)` (D1): arquivo vence env plana;
  ilegível/vazio → `ImproperlyConfigured`; sem arquivo → env plana (dev).

### R3 — Compose (D2/D3)

- Secret top-level `openrouter_api_key`
  (`file: ${OPENROUTER_API_KEY_FILE:-./secrets/openrouter_api_key.txt}`).
- `worker-llm` + `worker-attachments`: `secrets: [secret_key,
  app_db_password, openrouter_api_key]` (na ordem existente + novo),
  `OPENROUTER_API_KEY_FILE: /run/secrets/openrouter_api_key`,
  `LLM1_MODEL: ${LLM1_MODEL:-}`, `LLM2_MODEL: ${LLM2_MODEL:-}`,
  `VISION_MODEL: ${VISION_MODEL:-}` (attachments), `LLM_TIMEOUT_SECONDS:
  ${LLM_TIMEOUT_SECONDS:-120}`, `OPENROUTER_BASE_URL:
  ${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}` (llm; attachments se
  usar a base URL, idem).
- `deploy.resources.limits.memory` nos 4 workers (D3, valores exatos).
- Atualizar comentário do bloco workers (~203) e o header de secrets (~22)
  refletindo a fase 2.
- NENHUMA outra mudança no compose (user: ban, INTAKE_ENABLED intacto).

### R4 — Docs

- `.env.example`: seção "Fase 2 — pipeline (opcional até ativação)":
  `#OPENROUTER_API_KEY_FILE=./secrets/openrouter_api_key.txt`,
  `#LLM1_MODEL=`, `#LLM2_MODEL=`, `#VISION_MODEL=`, os 4 `#WORKER_*_MEM_LIMIT=`,
  nota de que `INTAKE_ENABLED=true` é a chave mestra.
- `README.md`: subseção "Ativação da fase 2 (checklist)" — segredo no
  arquivo (0644 ou chown 10001), modelos por `llm_check`, `up -d --profile
  workers`, `INTAKE_ENABLED=true up -d web`, rollback (env false + workers
  down; casos preservados).

### R5 — Testes (`apps/accounts/tests/test_prod_secrets.py`, seguindo os padrões existentes)

- Settings (espelham os de `SECRET_KEY_FILE`): arquivo vence env plana; env
  plana sem arquivo OK; sem nenhuma fonte → "" (não explode no import; o
  fail-fast é no USO); arquivo vazio/ilegível → `ImproperlyConfigured`.
- Compose guards (parse do YAML resolvido com TODOS os profiles):
  - `worker-llm`/`worker-attachments` têm `OPENROUTER_API_KEY_FILE` +
    secret montado + envs de modelo com passthrough;
  - `web`/`worker-pdf`/`worker-anonymization`/`migrate` SEM qualquer
    `OPENROUTER*`/`*_MODEL` (anti-vazamento de chave p/ serviço sem egress —
    não-vacuidade: mutação mental/temporária quebrar o teste);
  - secret `openrouter_api_key` definido com `file:` default
    `./secrets/openrouter_api_key.txt`;
  - os 4 workers têm `deploy.resources.limits.memory` com default não vazio;
  - `INTAKE_ENABLED` do web segue `${INTAKE_ENABLED:-false}`.
- Regressão: suíte de settings/compose existente 100% verde.

## Out of Scope

- Código do pipeline/vision; runbook de rede do hospital (infra Eon);
- CPU limits; fixar modelos; `INTAKE_ENABLED` default; sw.js/manual.

## Matriz requisito → arquivo → teste

| Requisito/spec | Código | Teste |
|---|---|---|
| Helper fail-closed | `config/settings/_secrets.py`, `db.py` | existentes (R1) verdes |
| `_FILE` precedência | `base.py` | test_prod_secrets (R5 settings) |
| Chave só em workers c/ egress | compose | guard anti-vazamento (R5) |
| Secret por arquivo | compose `secrets:` | guard file default (R5) |
| Limites mem 4 workers | compose | guard limits (R5) |
| Não ativa sozinho | compose `INTAKE_ENABLED` | guard default false (R5) |
| Docs fase 2 | .env.example/README | revisão parent |

## Verification (RED → GREEN, mesmo comando)

```bash
TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_prod_secrets.py -q
# GREEN total:
TEST_DB_PORT=55435 uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy .
# compose resolve com TODOS os profiles (usar os *_FILE de teste do host):
APP_DB_PASSWORD_FILE=... docker compose --profile migrate --profile workers \
  -f docker-compose.prod.yml --env-file .env.example config --quiet
```

## Expected files

- config/settings/_secrets.py (novo)
- config/settings/db.py
- config/settings/base.py
- docker-compose.prod.yml
- .env.example
- README.md
- apps/accounts/tests/test_prod_secrets.py

- allowed incidental files: NENHUM

## Acceptance criteria

- `secret_from_env` único ponto de leitura de arquivo de segredo (db.py
  reusando); testes antigos de DB settings verdes SEM edição.
- Guard anti-vazamento: nenhuma env OpenRouter em serviço sem egress.
- 4 workers com limite mem; defaults não vazios; envs tunáveis.
- INTAKE_ENABLED default false intacto; `user:` ban intacto.
- Suíte completa verde; ruff/format/mypy limpos; compose resolve all-profiles.

## Deviations / learnings

- (preenchido na execução)
