# Design: phase2-workers-secrets

## Context

- Broker = django-q2 com broker **ORM** (mesmo Postgres): não há
  RabbitMQ/Redis para segregar; worker fora do ar = tasks acumulam no ORM e
  rodam quando volta (sem perda).
- `apps/pipeline/llm.py` e `apps/attachments/vision.py` leem
  `settings.OPENROUTER_API_KEY` (env plana, default "" → fail-fast no uso).
- `config/settings/db.py` já tem o padrão puro de segredo-por-arquivo
  (precedência do arquivo, `ImproperlyConfigured` em ilegível/vazio),
  coberto por testes em `apps/accounts/tests/test_prod_secrets.py`.
- Redes: `hospital_egress_hmd` (saída restrita) já anexada a
  `worker-llm`/`worker-attachments`; `worker-pdf`/`worker-anonymization` não
  saem. `media_data` compartilhado pelos 4.
- Guards existentes: `test_prod_secrets.py` resolve o compose com TODOS os
  profiles, pinna secrets por consumidor e BANE `user:` no compose prod.

## Goals / Non-Goals

- **Goal**: workers prontos para a fase 2 — chave OpenRouter só por arquivo
  nos 2 workers com egress, modelos passáveis, limites de memória em todos.
- **Non-goal**: ativar (env/flags continuam false); tocar código do pipeline;
  definir modelos; CPU limits.

## Decisions

### D1 — Helper de segredo-por-arquivo extraído e reusado

`config/settings/_secrets.py`:

```python
def secret_from_env(env, *, secret_file_key, env_key, setting_name):
    """Arquivo tem precedência; ilegível/vazio → ImproperlyConfigured."""
```

`db.py` passa a usá-lo (comportamento idêntico — testes existentes pinam) e
`base.py` lê:

```python
OPENROUTER_API_KEY = secret_from_env(
    os.environ, secret_file_key="OPENROUTER_API_KEY_FILE",
    env_key="OPENROUTER_API_KEY", setting_name="OPENROUTER_API_KEY",
)
```

(env plana segue válida p/ dev/teste; arquivo vence quando presente — mesma
semântica de `SECRET_KEY_FILE`/`DB_PASSWORD_FILE`.)

### D2 — Compose: chave nos workers com egress, nada nos demais

- Secret top-level `openrouter_api_key`
  (`file: ${OPENROUTER_API_KEY_FILE:-./secrets/openrouter_api_key.txt}`).
- `worker-llm`/`worker-attachments`: montam o secret +
  `OPENROUTER_API_KEY_FILE: /run/secrets/openrouter_api_key` +
  `LLM1_MODEL`/`LLM2_MODEL`/`VISION_MODEL` via `${VAR:-}` + opcionais
  `LLM_TIMEOUT_SECONDS`/`OPENROUTER_BASE_URL` via `${VAR:-}`.
- `web`/`worker-pdf`/`worker-anonymization`/`migrate`: NADA de OpenRouter
  (o web nunca chama; pdf/anonymization não têm egress). Guard pinnado em
  teste — a chave não pode vazar para serviço sem egress.

### D3 — Limites de memória env-tunables nos 4 workers

`deploy.resources.limits.memory` (compose v2 honra fora de swarm):
`worker-pdf` `${WORKER_PDF_MEM_LIMIT:-512m}` · `worker-anonymization`
`${WORKER_ANONYMIZATION_MEM_LIMIT:-1536m}` (spaCy lg ~541 MB + Presidio +
folga) · `worker-llm` `${WORKER_LLM_MEM_LIMIT:-512m}` · `worker-attachments`
`${WORKER_ATTACHMENTS_MEM_LIMIT:-512m}`. Eon ajusta por env sem editar o
compose.

### D4 — Ativação continua 1 env

`INTAKE_ENABLED` do web permanece `${INTAKE_ENABLED:-false}`. Este change
deixa os workers CONFIGURÁVEIS; ligar é operação do host (runbook na
ativação: segredo no arquivo → .env → `up -d --profile workers` →
`INTAKE_ENABLED=true up -d web`). Rollback: env de volta a false + workers
down (casos já criados permanecem; FSM intacta).

## Risks / Trade-offs

- Limite apertado no anonymization mataria o worker em OOM → default folgado
  (1536m) e env-tunable; `ANONYMIZATION_BENCHMARK_MAX_RSS_MB` existe como
  critério operacional para calibrar.
- Passthrough de modelos com `${VAR:-}` vazio é seguro: settings ficam ""
  e o pipeline falha fechado com mensagem clara (`llm_check` reporta).

## Open Questions

Nenhuma — padrões já decididos nos changes anteriores (segredo por arquivo,
egress mínimo, fail-closed).
