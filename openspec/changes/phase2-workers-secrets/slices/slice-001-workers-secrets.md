# Slice 001 — Segredo OpenRouter por arquivo + modelos + limites nos workers

## Contexto necessário

- `_read_secret(env, secret_file_key) -> str | None` vive em
  `config/settings/db.py` (~27-44: arquivo vence; ilegível/vazio →
  `ImproperlyConfigured`; sem fonte → `None`) e é importado por
  `config/settings/prod.py` e
  `apps/accounts/management/commands/seed_admin.py` — a extração para
  `config/settings/_secrets.py` deve deixar `db.py` RE-EXPORTANDO
  `_read_secret` (consumidores externos intocados, testes existentes verdes).
- Settings em `config/settings/base.py` (~333-347): `OPENROUTER_API_KEY`
  hoje `os.environ.get(...)`. `LLM1_MODEL`/`LLM2_MODEL`/`VISION_MODEL`/
  `OPENROUTER_BASE_URL`/`LLM_TIMEOUT_SECONDS` permanecem como estão.
- Compose: `worker-llm`/`worker-attachments` na rede `hospital_egress_hmd`;
  `worker-anonymization`/`worker-pdf` sem egress; anonymization NÃO monta
  `media_data`. Estilo de limites do repo = `mem_limit:` (web :47-49).
  Secrets top-level ~:348. Comentário do bloco workers (~:203) e o de
  faixas de recurso (~:44-46) precisam de atualização.
- Consumidores: llm cluster lê `LLM1_MODEL`/`LLM2_MODEL`; attachments cluster
  lê `LLM1_MODEL` (verification) + `VISION_MODEL` + `OPENROUTER_BASE_URL`;
  vision lê `OPENROUTER_BASE_URL`. Cluster anonymization `workers: 2`
  (2 processos × engine singleton).
- Guards: `apps/accounts/tests/test_prod_secrets.py` (COMPOSE_FILE,
  `_deploy_artifact`, `_create_secret_dummies` ~:236-247, ban `user:` :625).
- `INTAKE_ENABLED` do web PERMANECE `${INTAKE_ENABLED:-false}` (D4).

## Goal

Workers prontos para a fase 2: chave só por arquivo nos 2 workers que chamam
a OpenRouter, modelos passáveis, limites de memória nos 4; nada ativa sozinho.

## Deliverables

### R1 — `_secrets.py` com primitiva + wrapper; `db.py` re-exporta

- `config/settings/_secrets.py`: `_read_secret` (semântica EXATA da atual,
  devolve `str | None`) e `secret_from_env(env, *, secret_file_key, env_key,
  setting_name) -> str` (sem nenhuma fonte → `""`).
- `db.py` usa a primitiva de `_secrets` e re-exporta `_read_secret`
  (`prod.py`/`seed_admin.py` intocados; suíte de DB settings verde SEM
  edição — a distinção `None`×`""` preserva o fail-closed de `DB_PASSWORD`
  vazia explícita).

### R2 — `OPENROUTER_API_KEY_FILE` no settings (base.py)

- `OPENROUTER_API_KEY = secret_from_env(os.environ, secret_file_key=
  "OPENROUTER_API_KEY_FILE", env_key="OPENROUTER_API_KEY", setting_name=
  "OPENROUTER_API_KEY")` — arquivo vence; ilegível/vazio →
  `ImproperlyConfigured`; sem arquivo → env plana (dev); sem nada → `""`.
- `config/settings/test.py`: pina `OPENROUTER_API_KEY_FILE`/`OPENROUTER_API_KEY`
  (suíte imune a env hostil — precedente `UNIT_LABELS`).

### R3 — Compose (D2/D3)

- Secret top-level `openrouter_api_key`
  (`file: ${OPENROUTER_API_KEY_FILE:-./secrets/openrouter_api_key.txt}`).
- `worker-llm`: secret `openrouter_api_key` + `OPENROUTER_API_KEY_FILE:
  /run/secrets/openrouter_api_key` + `LLM1_MODEL: ${LLM1_MODEL:-}` +
  `LLM2_MODEL: ${LLM2_MODEL:-}` + `OPENROUTER_BASE_URL:
  ${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}` +
  `LLM_TIMEOUT_SECONDS: ${LLM_TIMEOUT_SECONDS:-120}`.
- `worker-attachments`: secret + `OPENROUTER_API_KEY_FILE` + `LLM1_MODEL:
  ${LLM1_MODEL:-}` + `VISION_MODEL: ${VISION_MODEL:-}` + os mesmos
  `OPENROUTER_BASE_URL`/`LLM_TIMEOUT_SECONDS`.
- `worker-anonymization`: `ANONYMIZATION_SPACY_MODEL:
  ${ANONYMIZATION_SPACY_MODEL:-pt_core_news_lg}` (NADA de OpenRouter).
- `mem_limit: ${WORKER_PDF_MEM_LIMIT:-512m}` · `${WORKER_ANONYMIZATION_MEM_LIMIT:-2560m}`
  · `${WORKER_LLM_MEM_LIMIT:-512m}` · `${WORKER_ATTACHMENTS_MEM_LIMIT:-512m}`
  (estilo do web; NÃO usar `deploy:`).
- Atualizar comentários (~:44-46 faixas por worker ×2 processos; ~:203 envs
  da fase 2 agora fiadas; header de secrets ~:22).
- NENHUMA outra mudança (`user:` ban, `INTAKE_ENABLED` intacto).

### R4 — Docs

- `.env.example` seção "Fase 2 — pipeline": `#OPENROUTER_API_KEY_FILE=`,
  `#LLM1_MODEL=`, `#LLM2_MODEL=`, `#VISION_MODEL=`, 4 `#WORKER_*_MEM_LIMIT=`,
  `#ANONYMIZATION_SPACY_MODEL=`, nota de calibração (benchmark RSS ×2) e
  chave mestra `INTAKE_ENABLED`.
- `README.md`: subseção "Ativação da fase 2 (checklist)": (1) **pin da
  imagem NOVA primeiro** (helper viaja na imagem — workers em imagem velha
  falham `auth`); (2) segredo no arquivo (0644 ou chown 10001); (3) modelos
  no `.env` (`llm_check` via `docker compose --profile workers run --rm
  worker-llm python manage.py llm_check`; `VISION_MODEL` sem diagnóstico —
  conferir manualmente); (4) `up -d --profile workers`; (5)
  `INTAKE_ENABLED=true` + `up -d web`; rollback (env false + workers down,
  casos preservados). Ajustar frases stale (:160, ~:197 "na próxima
  mudança").

### R5 — Testes (`apps/accounts/tests/test_prod_secrets.py`, padrões existentes)

- Settings `OPENROUTER_API_KEY` (espelham SECRET_KEY): arquivo vence env;
  env plana sem arquivo OK; sem fonte → `""`; arquivo vazio/ilegível →
  `ImproperlyConfigured`.
- `db.py` pós-extração: testes existentes de DB verdes sem edição (regressão).
- Compose guards (YAML resolvido com dummies tmp + assert estático do
  `file:` NO FONTE p/ o secret novo):
  - `worker-llm`/`worker-attachments`: têm `OPENROUTER_API_KEY_FILE` +
    secret montado + seus modelos (llm: LLM1+LLM2; attachments: LLM1+VISION)
    com passthrough `${VAR:-}`;
  - `web`/`worker-pdf`/`worker-anonymization`/`migrate`: SEM qualquer
    `OPENROUTER*`/`*_MODEL` (anti-chave-em-serviço-sem-chamada; não-vacuidade
    por mutação temporária);
  - `worker-anonymization` tem `ANONYMIZATION_SPACY_MODEL` com default lg;
  - 4 workers com `mem_limit` não vazio (render normalizado em BYTES —
    não assertar literal `512m`); defaults conferíveis no FONTE;
  - `INTAKE_ENABLED` do web segue `${INTAKE_ENABLED:-false}`;
  - ban `user:` segue verde.

## Out of Scope

- Código do pipeline/vision; `seed_admin`/`prod.py` (só re-export os protege);
  runbook de rede do hospital (infra Eon); CPU/pids limits; dev compose
  (worker-llm dev sem passthrough — follow-up registrado); fixar modelos;
  `INTAKE_ENABLED` default.

## Matriz requisito → arquivo → teste

| Requisito/spec | Código | Teste |
|---|---|---|
| Primitiva+re-export | `_secrets.py`, `db.py` | DB settings existentes verdes |
| `_FILE` precedência/fail-closed | `base.py` | R5 settings |
| Suíte imune a env hostil | `test.py` | R5 (import sem crash) |
| Chave só em quem chama | compose | guard anti-vazamento |
| Secret por arquivo | compose | guard file (fonte+YAML) |
| SPACY_MODEL tunável | compose | guard default lg |
| Limites mem ×4 | compose | guard mem_limit |
| Não ativa sozinho | compose | guard INTAKE_ENABLED |
| Docs fase 2 | .env.example/README | revisão parent |

## Verification (RED → GREEN, mesmo comando)

```bash
TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_prod_secrets.py -q
# GREEN total:
TEST_DB_PORT=55435 uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy .
# compose resolve com TODOS os profiles (dummies do host) e SEM OPENROUTER_API_KEY_FILE:
APP_DB_PASSWORD_FILE=… docker compose --profile migrate --profile workers \
  -f docker-compose.prod.yml --env-file .env.example config --quiet
```

## Expected files

- config/settings/_secrets.py (novo)
- config/settings/db.py
- config/settings/base.py
- config/settings/test.py
- docker-compose.prod.yml
- .env.example
- README.md
- apps/accounts/tests/test_prod_secrets.py

- allowed incidental files: NENHUM

## Acceptance criteria

- `_read_secret` importável de `config.settings.db` (consumidores externos
  intactos); testes de DB settings verdes SEM edição.
- Guard anti-vazamento: nenhuma env OpenRouter em `web`/`worker-pdf`/
  `worker-anonymization`/`migrate`.
- 4 workers com `mem_limit` (anonymization 2560m default, ×2 processos
  documentado); SPACY_MODEL tunável.
- `INTAKE_ENABLED` default false intacto; ban `user:` intacto.
- Suíte completa verde; ruff/format/mypy limpos; compose resolve all-profiles
  com e sem `OPENROUTER_API_KEY_FILE` no ambiente.

## Deviations / learnings

- Executado 2026-09-14. RED 10 failed → GREEN 38 (`test_prod_secrets.py`);
  suíte 1175→1177; ruff/format/mypy limpos; compose resolve all-profiles com
  e sem `OPENROUTER_API_KEY_FILE`.
- Incidental aprovado pelo supervisor: `design.md` fence (ruff 0.16 formata
  fences Python em Markdown — 1 linha).
- Review (OK with notes) fechada pelo parent: F1 guard estático do `file:` do
  secret novo + passthrough `${VAR:-}` dos modelos no fonte; F2 imunidade
  real (pop das envs ANTES do `from .base import *` em test.py — o pin
  pós-import era inerte); F3 comentário stale v0.1.6→v0.1.7 no .env.example.
- Limitação registrada (F2 parcial): o pop cobre a SUÍTE; um `.env` real do
  host com `OPENROUTER_API_KEY_FILE` inválido continua fail-closed no dev
  (comportamento desejado).
