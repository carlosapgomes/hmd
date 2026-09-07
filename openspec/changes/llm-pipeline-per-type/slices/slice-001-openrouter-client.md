# Slice 001: Cliente OpenRouter + llm_check

## Objetivo

`apps/pipeline/llm.py`: cliente OpenAI-compatível apontando para a OpenRouter com **transport injetável** (suíte sem rede), erros tipados (`LlmError.kind`) e envs; comando manual `llm_check` (conectividade/auth por modelo). Também cria o app `apps/pipeline` e pina a dependência `openai`.

## Contexto necessário (contexto zero)

- Plano §7: "Cliente: SDK OpenAI → `OPENROUTER_API_KEY`/`OPENROUTER_BASE_URL`; modelos por env (`LLM1_MODEL`, `LLM2_MODEL`)" — candidatos flash citados mas escolha por benchmark OPERACIONAL (fora deste slice; env define).
- Padrão de injetabilidade do HMD: `KERBEROS_CLIENT_FACTORY` (change 02, `apps/accounts/kerberos.py`) — replicar o padrão: settings `LLM_CLIENT_FACTORY` (dotted path) com default para o cliente real; testes injetam fakes via `override_settings`.
- Referência de envs: ats-web `config/settings/base.py` (`OPENAI_API_KEY/MODEL/BASE_URL`) — HMD renomeia p/ OpenRouter e separa por estágio (LLM1/LLM2).
- `apps/pipeline` não existe — este slice a cria (app + INSTALLED_APPS). Sem models ainda.

## Requisitos

- **R1** `pyproject.toml`/lock: `openai` pinado (versão resolvida pelo uv; registrar no relatório).
- **R2** `LlmError(Exception)` com `kind ∈ {auth, rate_limit, network, timeout, invalid_response, other}` + mensagem interna (nunca logar conteúdo do payload/PII).
- **R3** `OpenRouterClient` (ou factory `get_llm_client()`): usa SDK OpenAI com `base_url=OPENROUTER_BASE_URL` (default `https://openrouter.ai/v1`), `api_key=OPENROUTER_API_KEY`, `timeout=LLM_TIMEOUT_SECONDS` (default 120); método `complete(model, messages, *, response_format=None) -> str` devolvendo o conteúdo; mapeia erros da SDK para `LlmError` com kind correto (401/403→auth; 429→rate_limit; `APITimeoutError`→timeout; `APIConnectionError`→network; demais→other). Cliente novo por chamada ou reuso seguro — documentar.
- **R4** Settings/env: `OPENROUTER_API_KEY` (default vazio), `OPENROUTER_BASE_URL`, `LLM1_MODEL`, `LLM2_MODEL` (defaults vazios — **fail-fast no uso**: pipeline do slice 004+ exige; llm_check reporta ausência), `LLM_TIMEOUT_SECONDS`, `LLM_CLIENT_FACTORY` (teste); `.env.example` documenta tudo.
- **R5** `manage.py llm_check [--models l1,l2]`: para cada modelo solicitado, 1 chamada mínima ("Responda: ok") via cliente; reporta modelo/latência/ok ou `LlmError.kind`+mensagem; **não requer banco de casos**; exit ≠ 0 se algum falhar. Manual — sem teste de rede no CI (testes usam fake factory).
- **R6** Testes (fakes, zero rede): mapeamento de cada kind (fake SDK levanta erros típicos); `complete` devolve conteúdo; factory injetável via settings; `llm_check` com fake ok (exit 0, latências) e com fake auth-fail (exit ≠ 0, kind reportado); modelo não configurado → reporta ausência.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `pyproject.toml`, `uv.lock` | `uv run python -c "import openai"` |
| R2/R3 | `apps/pipeline/llm.py` | `test_llm_client.py::test_error_kinds_mapping`, `::test_complete_returns_content` |
| R4 | `config/settings/base.py`, `.env.example` | `rg -n "OPENROUTER\|LLM1_MODEL\|LLM_TIMEOUT" config/settings/base.py .env.example` |
| R5 | `apps/pipeline/management/commands/llm_check.py` | `::test_llm_check_ok`, `::test_llm_check_auth_fail`, `::test_llm_check_missing_model` |
| R6 | `apps/pipeline/tests/test_llm_client.py` | `uv run pytest apps/pipeline/tests/test_llm_client.py` |

## RED

- Comando: `uv run pytest apps/pipeline/tests/test_llm_client.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.pipeline'`.

## GREEN / verificação local

- `uv run pytest apps/pipeline/tests/test_llm_client.py` — exit 0
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0
- `uv run python manage.py llm_check --help` (sem rede)

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/pipeline/{__init__,apps,llm}.py
  - apps/pipeline/management/{__init__,commands/{__init__,llm_check}}.py
  - apps/pipeline/tests/{__init__,test_llm_client}.py
  - config/settings/base.py
  - pyproject.toml
  - uv.lock
  - .env.example
out_of_scope:
  - schemas/prompts/serviços (002+); models/migrations; cluster/worker (006)
  - qualquer chamada de rede em teste
```

Escale ao parent se: a API da SDK openai resolvida divergir do mapeamento de erros esperado.

## Critérios de aceitação

- [ ] R1–R6 comprovados; suíte 100% sem rede
- [ ] Kinds mapeados por tipo de erro da SDK
- [ ] Gate parcial do slice verde
