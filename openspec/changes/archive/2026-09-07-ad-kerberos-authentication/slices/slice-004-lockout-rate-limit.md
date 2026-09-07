# Slice 004: Proteção anti-lockout local

## Objetivo

Rate-limit de tentativas de login malsucedidas por CPF e por IP+CPF no cache do Django, com limiar/janela/duração configuráveis. Comportamento observável: atingido o limiar, novas tentativas são recusadas com mensagem genérica **antes** de consultar qualquer backend/KDC; sucesso zera contadores; `account_status` não é alterado.

## Contexto necessário (contexto zero)

- Slices 001–003 deste change entregues: ad_upn/admin, kerberos.py, KerberosBackend + login com mensagens distintas.
- Motivação (fonte autoritativa): `temp/research/kerberos-django.md`, seção "Rate limiting e lockout AD" — cada tentativa com senha errada pode contar para o lockout do AD; limite local deve ficar abaixo da política do domínio; não revelar existência de CPF.
- Design: `../design.md` D7 (cache Django, env-configurável, recusa pré-KDC, temporário).
- Spec: Requirement ADDED "Proteção anti-lockout local" (2 cenários).
- O cache default atual é o `LocMemCache` do Django (nada a configurar em dev/teste; prod configura backend próprio quando existir).

## Requisitos

- **R1** `apps/accounts/ratelimit.py`: funções puras sobre o cache — registrar falha (por `cpf` **normalizado** strip/lower e por `ip+cpf`), checar bloqueio, zerar contadores — com chaves namespaceadas e TTL derivado da janela/duração. O IP vem do **mesmo helper confiável do guard de intranet** (`_get_client_ip` com `TRUSTED_PROXY_HEADER`, reutilizado/importado de `apps/accounts/middleware.py`; nunca header arbitrário).
- **R2** Integração no fluxo de login: **antes** de acionar backends, checa bloqueio; bloqueado → recusa com a mesma mensagem genérica de credenciais/serviço (sem revelar bloqueio nem consultar KDC); tentativa malsucedida registra falha — **exceto quando a falha é por indisponibilidade do serviço** (`request.kerberos_unavailable` setado: não chegou ao AD, não conta para o limite); bem-sucedida zera.
  - *Emenda pós-review (2026-09-06, aprovada pelo dono): a exceção de indisponibilidade foi adicionada após o gate final, registrada na spec e no tasks.md (4.2).*
- **R3** Settings por env: `LOGIN_ATTEMPTS_LIMIT` (default 5), `LOGIN_ATTEMPTS_WINDOW_SECONDS` (default 900), `LOGIN_LOCKOUT_SECONDS` (default 900) — documentadas no `.env.example`.
- **R3b** `config/settings/prod.py` falha fechado (`ImproperlyConfigured` com mensagem clara) quando `CACHES` ainda for o default `LocMemCache` em produção — cache compartilhado (Redis/Memcached) é obrigatório no deploy para o limiar valer entre workers; dev/teste permanecem LocMem.
- **R4** O bloqueio é temporário (expira sozinho via TTL do cache) e **não** altera `account_status` nem persiste no banco.
- **R5** Testes: atingir limiar → próxima tentativa negada sem acionar a fake do Kerberos (assert de não-chamada); dentro do limiar não bloqueia; sucesso zera (nova falha conta de 1); entradas por IP+CPF independentes do contador por CPF; expiração simulada (TTL curto em override_settings ou tempo do cache zerado); **variações do CPF (espaços/caixa) contam para o mesmo contador**; **IP obtido do header confiável** (spoof de header não-confiável não muda a chave); settings `prod` com LocMem → `ImproperlyConfigured`; **N tentativas de indisponibilidade acima do limiar NÃO bloqueiam** (login posterior válido bem-sucedido).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/ratelimit.py` | `test_lockout.py::test_counters_by_normalized_cpf_and_trusted_ip` |
| R2 | `apps/accounts/views.py` (ou backend, conforme design do fluxo) | `test_lockout.py::test_locked_attempt_skips_kerberos`, `::test_success_resets_counters`, `::test_service_unavailable_failures_do_not_lock` |
| R3/R3b | `config/settings/{base,prod}.py`, `.env.example` | `rg -n "LOGIN_ATTEMPTS_LIMIT|LOGIN_ATTEMPTS_WINDOW|LOGIN_LOCKOUT" config/settings/base.py .env.example` + `::test_prod_requires_shared_cache` |
| R4 | `apps/accounts/ratelimit.py` | `::test_lockout_expires_without_touching_account_status` |
| R5 | `apps/accounts/tests/test_lockout.py` | `uv run pytest apps/accounts/tests/test_lockout.py` |

## RED

- Comando: `uv run pytest apps/accounts/tests/test_lockout.py`
- Falha esperada: `ModuleNotFoundError`/assert — a 6ª tentativa ainda aciona a fake do Kerberos (proteção inexistente).

## GREEN / verificação local

- `uv run pytest apps/accounts/tests/test_lockout.py` — exit 0
- `uv run pytest apps/accounts/tests/` — exit 0 (regressão do app)
- `uv run ruff check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/ratelimit.py
  - apps/accounts/views.py        # integração mínima no fluxo de login
  - apps/accounts/tests/test_lockout.py
  - config/settings/base.py
  - config/settings/prod.py
  - .env.example
allowed_incidental_files:
  - apps/accounts/tests/conftest.py (cache limpo entre testes, se necessário)
out_of_scope:
  - backend Redis/memcached (apenas a exigência fail-closed entra; a escolha/instalação do backend é do deploy)
  - backoff progressivo/jitter (melhoria futura; janela fixa atende a spec)
  - dashboard/métricas de tentativas
  - qualquer persistência em banco (account_status intocável)
```

Escale ao parent se: a integração exigir mudar o contrato do `KerberosBackend` (auth hooks do Django); precisar de cache não-LocMem em dev/teste.

## Critérios de aceitação

- [ ] R1–R5 comprovados pelos comandos da matriz (2 cenários da spec cobertos)
- [ ] Bloqueio não consulta KDC (assert de não-chamada comprovado)
- [ ] `account_status` intocado pelo rate-limit
- [ ] Gate parcial do slice verde
