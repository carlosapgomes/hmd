# Slice 001 — Guard decide pelo conjunto de papéis

## Objetivo

O `IntranetGuardMiddleware` passa a bloquear o acesso externo apenas quando o
**conjunto de papéis** do usuário contém exclusivamente papéis restritos
(`INTRANET_RESTRICTED_ROLES`). Um usuário com qualquer papel fora do conjunto
restrito acessa de qualquer rede mesmo com papel ativo restrito. Sem mudança
nos caminhos isentos, na leitura de IP ou no default de dev.

## Contexto necessário (ler antes de editar)

- `apps/accounts/middleware.py` — `IntranetGuardMiddleware` inteiro (~85-135):
  a regra 2 do docstring e o ponto de decisão em `__call__`.
- `apps/accounts/tests/test_intranet_guard.py` — helpers `_create_user`,
  `_login_with_active_role`, constantes (`EXTERNAL_IP`, `INTRANET_CIDR`,
  `GUARD_SETTINGS`, `BLOCK_MESSAGE_FRAGMENT`) e o teste
  `test_multi_role_blocked_on_nir_and_released_after_switch` (será reescrito).
- `openspec/changes/intranet-any-role-egress/design.md` — D1 (tabela de casos),
  D2 (ordem/custo), D5 (log inalterado).
- `docs/adr/ADR-0002-papeis-fixos-intranet-guard-nir-ativo.md` — ADR inteiro
  (curto): a decisão revertida está na seção "Decision".
- View usada pelos testes existentes para provar "não isenta": a própria
  fixture de teste já resolve (clientes autenticados acessam `/` — mantenha o
  padrão dos testes vizinhos).

## Requisitos verificáveis

- **R1** — Usuário cujo conjunto de papéis é apenas `nir`, papel ativo `nir`,
  IP externo → 403 com a mensagem atual (comportamento central preservado).
- **R2** — Usuário com conjunto `{nir, manager}`, papel ativo `nir`, IP
  externo → acesso prossegue (sem trocar de papel). O teste antigo
  `test_multi_role_blocked_on_nir_and_released_after_switch` é substituído por
  este cenário.
- **R3** — `INTRANET_RESTRICTED_ROLES = ["nir", "scheduler"]` (multi-valor):
  usuário `{nir, scheduler}` com papel ativo `nir` (e também com ativo
  `scheduler`) + IP externo → 403 nos dois casos (conjunto inteiro restrito).
- **R4** — Regras vizinhas intocadas: paths isentos, `INTRANET_IP_RANGE`
  vazio não bloqueia (dev), IP de intranet passa com papel ativo `nir`,
  `TRUSTED_PROXY_HEADER` segue sendo a fonte do IP (testes existentes devem
  continuar passando sem edição, salvo o cenário reescrito em R2).
- **R5** — Documentação coerente: docstring do middleware descreve a regra
  nova (a nota de divergência "segue o papel ativo, nunca o conjunto" é
  removida/substituída) e o ADR-0002 ganha seção "## Revisão (2026-09-13)"
  registrando a reversão daquela decisão (decisão do dono pós-piloto;
  `## Status` vira `Accepted (revisado em 2026-09-13)`).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/middleware.py` | `test_intranet_guard.py::test_nir_blocked_outside_range` (existente, sem edição) |
| R2 | `apps/accounts/middleware.py` | `test_intranet_guard.py::test_multi_role_with_external_role_passes_with_active_nir` (novo, substitui o teste da troca de papel) |
| R3 | `apps/accounts/middleware.py` | `test_intranet_guard.py::test_all_roles_restricted_stays_blocked` (novo, com override de `INTRANET_RESTRICTED_ROLES`) |
| R4 | `apps/accounts/middleware.py` | testes existentes: `test_exempt_paths_never_blocked`, `test_no_range_configured_does_not_block`, `test_trusted_proxy_header_used`, `test_nir_from_intranet*` (se existir) — sem edição |
| R5 | `apps/accounts/middleware.py`, `docs/adr/ADR-0002-…md` | inspeção: docstring sem a frase da divergência ativa; ADR com seção de revisão |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/middleware.py
  - apps/accounts/tests/test_intranet_guard.py
  - docs/adr/ADR-0002-papeis-fixos-intranet-guard-nir-ativo.md

allowed_incidental_files: []

out_of_scope:
  - settings novos ou renomeados (INTRANET_RESTRICTED_ROLES/INTRANET_IP_RANGE mantêm forma e defaults)
  - UI, templates, manual, migrations, models
  - mudanças em lockout/ratelimit ou backends de auth
  - refactor de ActiveRoleMiddleware ou dos helpers de IP (_get_client_ip/_is_intranet_ip)
```

Escalamento: se a mudança exigir tocar qualquer item de `out_of_scope`, ou se
os testes vizinhos (`test_active_role`, `test_auth_flow`) revelarem dependência
na semântica antiga, **pare e reporte** — não amplie o slice.

## Notas de implementação

- Nova checagem imediatamente antes do `logger.warning`/403, após IP de
  intranet (design D2): `user.roles.exclude(name__in=restritos).exists()` →
  passa se existir papel fora do conjunto restrito.
- Não introduza cache da consulta; o caminho é raro por construção.
- Atualize a lista numerada do docstring (a regra 2 e a nota de divergência
  citam o comportamento antigo).

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_intranet_guard.py -q`
- falha esperada: `test_multi_role_with_external_role_passes_with_active_nir` e
  `test_all_roles_restricted_stays_blocked` FALHAM no código atual (o
  middleware bloqueia multi-role com ativo `nir` — 403 onde o teste espera
  sucesso; o cenário multi-valor nem existe).

### GREEN

- mesmo comando — 0 failed (todos os testes do arquivo, incluindo os
  existentes de R4).

### Verificação do slice

- `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_intranet_guard.py apps/accounts/tests/test_active_role.py -q` — 0 failed
- `uv run ruff check apps/accounts/middleware.py apps/accounts/tests/test_intranet_guard.py && uv run ruff format --check apps/accounts/middleware.py apps/accounts/tests/test_intranet_guard.py` — ok
- `uv run mypy apps/accounts` — ok
- Suíte completa fica para o gate final do change.

## Critérios de aceitação

- [ ] R1–R5 verdes conforme a matriz acima
- [ ] Nenhum arquivo fora do blast radius
- [ ] RED demonstrado antes do GREEN (mesmo comando)
- [ ] Docstring e ADR coerentes com a regra nova
