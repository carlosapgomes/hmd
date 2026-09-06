# Slice 006: Intranet guard restrito a `nir` + gate final do change

## Objetivo

Implementar o `IntranetGuardMiddleware` bloqueando o papel ativo `nir` fora da faixa de intranet configurada (demais papéis liberados de qualquer rede), com bypass multi-role, paths isentos e trusted proxy header. Conclui o change: após GREEN, o parent executa o quality gate completo e o arquivamento.

## Contexto necessário (contexto zero)

- Slices 001–005 entregues: scaffold, banco, accounts completos (modelos, auth local, papel ativo em sessão, switch-role, `role_required`).
- Referência somente-leitura: `/projects/dev/ats-web/apps/accounts/middleware.py` (`IntranetGuardMiddleware`, `_get_client_ip` com `TRUSTED_PROXY_HEADER`, checagem de CIDR, paths isentos — **atenção às duas divergências deliberadas do HMD**: (1) no ats-web `INTRANET_RESTRICTED_ROLES` é constante no próprio middleware e o bypass considera o **conjunto** de papéis; no HMD vira setting por env e a restrição segue o **papel ativo**, com `/switch-role/` isento como rota de fuga; (2) no HMD só `nir` é restrito — scheduler acessa via internet, unidade 2), `docker-compose*.yml`.
- Design: `../design.md` D5. ADR-0002 registra a decisão.
- Spec: `../specs/account-access/spec.md` (requisito "NIR restrito à rede interna" — 3 cenários: bloqueio, doctor liberado, bypass multi-role).
- Variáveis: `INTRANET_RESTRICTED_ROLES` (default `nir`), `INTRANET_IP_RANGE` (CIDRs separadas por vírgula; vazio = sem restrição em dev), `TRUSTED_PROXY_HEADER` (default `HTTP_CF_CONNECTING_IP`).

## Requisitos

- **R1** `IntranetGuardMiddleware`: papel ativo ∈ `INTRANET_RESTRICTED_ROLES` e IP fora de `INTRANET_IP_RANGE` → resposta de bloqueio (página/HTTP com mensagem clara), sem stack trace.
- **R2** IP de origem: usa `TRUSTED_PROXY_HEADER` quando presente e confiável; fallback `REMOTE_ADDR`.
- **R3** Paths isentos: login, logout, switch-role (e static/media) nunca bloqueados.
- **R4** Restrição pelo **papel ativo** (divergência deliberada do ats-web, que bypassa pelo conjunto): usuário multi-role `nir`+`manager` com papel ativo `nir` é bloqueado externamente; a troca para `manager` via `/switch-role/` (path isento, R3) reabilita o acesso externo. Nenhum bypass por conjunto de papéis.
- **R5** Ordenação: guard roda **após** `ActiveRoleMiddleware` (precisa do papel ativo) — registrado e documentado no settings.
- **R6** `.env.example` documenta as três variáveis; testes cobrem os 3 cenários da spec com IPs/headers fabricados.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/middleware.py` | `test_intranet_guard.py::test_nir_blocked_outside_range` |
| R2 | `apps/accounts/middleware.py` | `::test_trusted_proxy_header_used` |
| R3 | `apps/accounts/middleware.py` | `::test_exempt_paths_never_blocked` |
| R4 | `apps/accounts/middleware.py` | `::test_multi_role_blocked_on_nir_and_released_after_switch` (cenário 3 da spec) |
| R5 | `config/settings/base.py` | inspeção da ordem em `MIDDLEWARE` |
| R6 | `.env.example`, `apps/accounts/tests/test_intranet_guard.py` | `rg -n "INTRANET_" .env.example` + `uv run pytest apps/accounts/tests/test_intranet_guard.py` |

## RED

- Comando: `uv run pytest apps/accounts/tests/test_intranet_guard.py`
- Falha esperada: `ImportError`/assert — middleware não existe; requisição de `nir` externo retorna 200 (comportamento não implementado).

## GREEN / verificação local

- `uv run pytest apps/accounts/tests/test_intranet_guard.py` — exit 0
- `uv run pytest apps/accounts/tests/` — exit 0 (regressão de todos os slices de accounts)
- `uv run ruff check . && uv run mypy .` — exit 0

## Gate final do change (executado pelo parent, não pelo worker)

Após review do slice aceito, o parent executa uma única vez e registra no relatório do change:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest
```

Critério: os 4 comandos exit 0. Em seguida: atualizar `tasks.md` (7.1/7.2), atualizar `PROJECT_CONTEXT.md` com o estado pós-change e arquivar (`openspec archive bootstrap-django-hmd-core`).

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/middleware.py        # + IntranetGuardMiddleware
  - config/settings/base.py            # INTRANET_* + ordem MIDDLEWARE
  - .env.example
  - apps/accounts/tests/test_intranet_guard.py
allowed_incidental_files: []
out_of_scope:
  - qualquer alteração em views/templates existentes além do estritamente necessário
  - auth AD/Kerberos (change 02)
  - documentação de deploy/prod runbook (change futuro)
```

Escale ao parent se: a checagem de CIDR exigir dependência externa (implemente com `ipaddress` da stdlib); precisar bloquear papéis além de `nir` (não amplie `INTRANET_RESTRICTED_ROLES` sem confirmar com o dono).

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz (3 cenários da spec cobertos)
- [ ] Dev sem `INTRANET_IP_RANGE` configurada não é bloqueado (default seguro p/ desenvolvimento)
- [ ] Gate parcial do slice verde
- [ ] Gate final do change executado pelo parent após review
