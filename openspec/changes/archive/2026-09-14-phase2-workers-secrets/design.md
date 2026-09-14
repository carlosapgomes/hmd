# Design: phase2-workers-secrets

## Context

- Broker = django-q2 com broker **ORM** (mesmo Postgres): não há
  RabbitMQ/Redis para segregar; worker fora do ar = tasks acumulam no ORM e
  rodam quando volta (sem perda).
- `apps/pipeline/llm.py` e `apps/attachments/vision.py` leem a SETTING
  `OPENROUTER_API_KEY` (hoje env plana, default "" → fail-fast no uso).
- O padrão de leitura por arquivo vive em `config/settings/db.py` como
  `_read_secret(env, secret_file_key) -> str | None` (arquivo vence;
  ilegível/vazio → `ImproperlyConfigured`; sem fonte → `None`), com DOIS
  consumidores além do próprio `db.py`: `config/settings/prod.py` e
  `apps/accounts/management/commands/seed_admin.py` importam
  `from config.settings.db import _read_secret` — a extração PRECISA manter o
  símbolo importável dali (re-export).
- Redes: `hospital_egress_hmd` (saída restrita) anexada a `web` (AD/DNS/
  Kerberos), `worker-llm` e `worker-attachments`. `worker-pdf`/
  `worker-anonymization` não saem (trabalham sobre o DB; anonymization consome
  `case.extracted_text` e NÃO monta `media_data`).
- Consumidores por env: `LLM1_MODEL`/`LLM2_MODEL` no cluster llm;
  `LLM1_MODEL` (verification) + `VISION_MODEL` + `OPENROUTER_BASE_URL` no
  cluster attachments; `OPENROUTER_BASE_URL` também no vision. Nenhum caminho
  de runtime no `web` chama OpenRouter (tasks/management commands apenas;
  prod força os 4 `*_RUN_TASKS_INLINE=false`).
- Guards existentes: `test_prod_secrets.py` resolve o compose com TODOS os
  profiles, pinna secrets por consumidor e BANE `user:` no compose prod.
- Recursos hoje: só o `web` tem limites (`mem_limit: 512m`, `cpus: "1.0"`,
  `pids_limit: 200`) — estilo `mem_limit`, que adotamos (compatível v1/v2;
  `deploy.*` seria ignorado silenciosamente pelo compose v1 fora de swarm).
- Cluster `anonymization` roda `workers: 2` — o engine (spaCy lg + Presidio)
  é singleton POR PROCESSO (`engine.py` `@lru_cache`), então o container
  steady-state carrega 2 cópias do modelo.

## Goals / Non-Goals

- **Goal**: workers prontos para a fase 2 — chave OpenRouter só por arquivo
  nos 2 workers que chamam a OpenRouter, modelos passáveis, limites de
  memória em todos.
- **Non-goal**: ativar (env/flags continuam false); tocar código do pipeline;
  definir modelos; CPU limits; dev compose (worker-llm do dev sem passthrough
  — registrado como follow-up, fora deste change).

## Decisions

### D1 — Primitiva e wrapper em `_secrets.py`; `db.py` re-exporta

`config/settings/_secrets.py`:

```python
def _read_secret(env, secret_file_key) -> str | None:
    """Arquivo tem precedência; ilegível/vazio → ImproperlyConfigured;
    sem fonte → None (distinção preservada p/ callers com default)."""


def secret_from_env(env, *, secret_file_key, env_key, setting_name) -> str:
    """Wrapper p/ settings planas: sem NENHUMA fonte → "" (fail-fast no uso)."""
```

`db.py` importa ambos de `_secrets` e MANTÉM `_read_secret` importável
(re-export) — `prod.py` e `seed_admin.py` intocados, testes existentes verdes
sem edição. `_read_secret` permanece a ÚNICA implementação de leitura.

### D2 — Chave apenas nos processos que chamam a OpenRouter

Secret top-level `openrouter_api_key`
(`file: ${OPENROUTER_API_KEY_FILE:-./secrets/openrouter_api_key.txt}`)
montado SOMENTE em `worker-llm` e `worker-attachments` (o `web` está na rede
de egress por AD/DNS/Kerberos mas NÃO chama OpenRouter — fica sem chave).
Env mapping EXPLÍCITO:
- `worker-llm`: `OPENROUTER_API_KEY_FILE`, `LLM1_MODEL`, `LLM2_MODEL`,
  `OPENROUTER_BASE_URL`, `LLM_TIMEOUT_SECONDS`
- `worker-attachments`: `OPENROUTER_API_KEY_FILE`, `LLM1_MODEL`,
  `VISION_MODEL`, `OPENROUTER_BASE_URL`, `LLM_TIMEOUT_SECONDS`
- `worker-anonymization` ganha `ANONYMIZATION_SPACY_MODEL:
  ${ANONYMIZATION_SPACY_MODEL:-pt_core_news_lg}` (mitigação documentada de
  memória: lg→md em host enxuto) — SEM nada de OpenRouter
- `web`/`worker-pdf`/`migrate`: SEM qualquer env OpenRouter (guard pinnado)

### D3 — Limites `mem_limit` env-tunables, orçamento ×processos

`mem_limit: ${WORKER_*_MEM_LIMIT:-…}` (estilo do `web`, v1/v2-agnóstico):
pdf 512m · **anonymization 2560m** (cluster `workers: 2` × engine
singleton/processo ≈ 2 cópias do modelo lg + monitor/pusher + folga — default
conservador; calibrar no host com `anonymization_benchmark --corpus real`
medindo pico de RSS × 2 antes/na ativação; se apertado, alternativas:
 subir o env OU `ANONYMIZATION_SPACY_MODEL=pt_core_news_md`) · llm 512m ·
attachments 512m. `config/settings/test.py` pina
`OPENROUTER_API_KEY_FILE`/`OPENROUTER_API_KEY` (imunidade da suíte a envs
hostis — precedente `UNIT_LABELS`).

### D4 — Ativação continua 1 env + exige imagem nova

`INTAKE_ENABLED` do web permanece `${INTAKE_ENABLED:-false}`. A ativação no
host exige: **pin da imagem nova (v0.1.8) ANTES de subir os workers** (o
helper `_FILE` viaja NA imagem — workers em imagem velha falhariam fechado
com `auth`), segredo no arquivo, modelos no `.env`,
`up -d --profile workers`, `INTAKE_ENABLED=true` + `up -d web`. Rollback:
env false + workers down (casos preservados, FSM intacta).

## Risks / Trade-offs

- OOM no anonymization mata o worker em meio a task → caso `FAILED`
  (fail-closed, visível): default 2560m + calibração por benchmark no host +
  fallback md por env (D3).
- Passthrough vazio de modelos é seguro: settings "" → pipeline falha
  fechado com mensagem clara; `llm_check` (rode DENTRO do worker:
  `docker compose --profile workers run --rm worker-llm python manage.py
  llm_check`) reporta l1/l2; `VISION_MODEL` não tem comando de diagnóstico —
  conferir no `.env` (checklist).
- Guard de compose: assert estático do `file:` no FONTE + YAML resolvido com
  dummy tmp (o compose stat do arquivo de secret no `config` sem o env pode
  falhar — `./secrets/` não existe no repo).

## Open Questions

Nenhuma — padrões já decididos nos changes anteriores (segredo por arquivo,
egress mínimo, fail-closed).
