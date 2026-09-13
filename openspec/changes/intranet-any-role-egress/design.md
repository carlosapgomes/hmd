# Design — intranet-any-role-egress

## D1 — Regra pelo conjunto de papéis (decisão do dono, 2026-09-13)

Bloqueio externo ⇔ `papel ativo ∈ INTRANET_RESTRICTED_ROLES` **E** o usuário
**não** tem nenhum papel fora de `INTRANET_RESTRICTED_ROLES`. Equivalentemente:
o conjunto de papéis do usuário é subconjunto dos restritos. Casos:

| Conjunto do usuário | Papel ativo | IP externo | Resultado |
| --- | --- | --- | --- |
| `{nir}` | `nir` | sim | **403** (regra central preservada) |
| `{nir, manager}` | `nir` | sim | **passa** (novo — fim da fricção) |
| `{nir, scheduler}`, restritos=`[nir, scheduler]` | `nir` ou `scheduler` | sim | **403** (todos os papéis são restritos) |
| `{doctor}` | `doctor` | sim | passa (igual a hoje) |

O gatilho continua sendo o papel ativo restrito (regra 2 do middleware): a
consulta ao conjunto só tem sentido nesse ramo.

## D2 — Ordem das checagens e custo

A nova checagem entra **depois** de: anônimo, papel ativo não restrito, paths
isentos, faixa vazia (default de dev) e IP de intranet — imediatamente antes do
bloqueio. Assim a query `user.roles.exclude(name__in=restritos).exists()` roda
**apenas** no caminho que bloquearia hoje (raro): zero queries novas no fluxo
comum e para usuários na intranet.

## D3 — Semânticas de borda preservadas

- `INTRANET_RESTRICTED_ROLES` vazio: ninguém é bloqueado (conjunto não-vazio
  nunca é subconjunto do vazio; usuários sem papéis já param na regra do papel
  ativo — sessão sem `active_role` válido).
- `INTRANET_IP_RANGE` vazio (default de dev): restrição desligada (R6 intacto).
- Paths isentos (login/logout/switch-role, static/media) e leitura de IP pelo
  cabeçalho confiável (`TRUSTED_PROXY_HEADER`) não mudam.

## D4 — Spec e ADR

- Spec `account-access`: requisito "NIR restrito à rede interna" REMOVED +
  requisito novo "Acesso externo restrito a conjuntos exclusivamente
  restritos" ADDED — cenário da troca de papel substituído pelo inverso
  (multi-role com ativo `nir` passa) e cenário novo do conjunto inteiro
  restrito.
- ADR-0002: a divergência vs ats-web ("restrição segue o papel ativo, nunca o
  conjunto") era explícita e aceita; a decisão do dono pós-piloto a **reverte**.
  O ADR ganha seção "Revisão (2026-09-13)" registrando a reversão — ADRs não
  são editados silenciosamente.

## D5 — Log de bloqueio inalterado

`intranet_guard_blocked user=<pk> role=<ativo> ip=<ip> path=<path>` mantido
(pk, sem CPF/username) — só o comportamento que leva a ele muda.
