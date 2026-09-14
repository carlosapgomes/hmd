# Proposal: phase2-workers-secrets

## Why

A fase 2 do piloto (ativação do intake + pipeline) exige que os workers
`worker-llm` e `worker-attachments` alcancem a OpenRouter — mas o compose
produção NÃO fia a chave nem os modelos nesses serviços (comentário próprio:
"As envs OPENROUTER_API_KEY/LLM*_MODEL/VISION_MODEL NÃO entram na fase 1 —
ativam junto com este profile e a rede de egress na próxima mudança"), e a
convenção do repo é segredo 100% por arquivo. Também falta qualquer limite de
recurso nos 4 workers (host compartilhado do hospital; o worker de
anonimização carrega spaCy lg ~541 MB).

## What Changes

- `OPENROUTER_API_KEY_FILE` no settings com precedência sobre a env plana,
  fail-closed (arquivo ilegível/vazio → `ImproperlyConfigured`), reusando o
  padrão puro já provado em `config/settings/db.py` (helper extraído para
  módulo próprio).
- Compose produção: novo secret `openrouter_api_key`
  (`${OPENROUTER_API_KEY_FILE:-./secrets/openrouter_api_key.txt}`) montado
  SÓ em `worker-llm` e `worker-attachments` (únicos com egress); envs
  `LLM1_MODEL`/`LLM2_MODEL`/`VISION_MODEL` (+`LLM_TIMEOUT_SECONDS`/
  `OPENROUTER_BASE_URL` opcionais) com passthrough `${VAR:-}` nesses mesmos
  dois serviços.
- `deploy.resources.limits` (memória, env-tunable com default) nos 4 workers:
  pdf 512m · anonymization 1536m · llm 512m · attachments 512m.
- `.env.example` (seção fase 2: chave por arquivo + modelos obrigatórios +
  limites) e README (checklist de ativação fase 2 + rollback por env).
- `INTAKE_ENABLED` PERMANACE `${INTAKE_ENABLED:-false}` — a ativação em si
  continua sendo 1 env no host (decisão de operação, não de release).

## Capabilities

### Modified: `production-deployment`

- ADDED requirement "Workers do pipeline com segredo por arquivo e limites
  de recurso" (cenários: chave só por arquivo nos workers com egress;
  workers sem egress não recebem a chave; limites presentes; ativação/rollback
  por env).

## Impact

- Sem mudança de runtime da fase 1: web/migrate/pdf/anonymization intactos
  (pdf/anonymization não ganham egress nem chave); default continua
  desligado. Ativação real = operação no host (runbook), fora deste change.
- Arquivos: `config/settings/_secrets.py` (novo) + `base.py`/`db.py` (usar o
  helper) + `docker-compose.prod.yml` + `.env.example` + `README.md` + testes
  (`test_prod_secrets.py` estendido).

## Não-goais

- Não ativa nada por padrão; não cria runbook de rede do hospital (infra
  Eon); não fixa modelos (escolha por `llm_check` operacional); não mexe em
  LIMITES de CPU (memória basta para o risco do host compartilhado).
