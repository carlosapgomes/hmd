# Change: intranet-any-role-egress

## Why

O piloto de produção (v0.1.3, autenticando via AD desde 2026-09-13) expôs uma
regra operacional indesejada: o guard de intranet bloqueia pelo **papel ativo**
da sessão. Um usuário multi-role (ex.: `nir` + `manager`) acessando de fora da
rede com papel ativo `nir` é bloqueado, e a única rota de fuga é trocar de
papel — fricção real no dia a dia do hospital (decisão do dono, 2026-09-13).

Regra desejada: **o bloqueio externo vale apenas para quem só tem papéis
restritos** (`nir` puro → sempre restrito à intranet); **qualquer papel com
permissão de acesso externo no conjunto libera o acesso**, mesmo com papel
ativo restrito.

## What Changes

- `IntranetGuardMiddleware` passa a decidir pelo **conjunto de papéis** do
  usuário: bloqueia externamente somente quando o conjunto ⊆
  `INTRANET_RESTRICTED_ROLES` (avaliado apenas no caminho que hoje bloquearia).
- Spec `account-access`: requisito "NIR restrito à rede interna" **REMOVED** e
  substituído por "Acesso externo restrito a conjuntos exclusivamente
  restritos" — o eixo da regra mudou (papel ativo → conjunto de papéis), então
  o cenário da rota de fuga por troca de papel é substituído pelo seu inverso
  (multi-role com papel ativo `nir` acessa externamente) + cenário novo do
  conjunto inteiro restrito. REMOVED+ADDED em vez de MODIFIED porque o
  validador exige que MODIFIED carregue cenários cujo comportamento deixou de
  existir.
- ADR-0002 revisto: a divergência deliberada vs ats-web ("restrição segue o
  papel ativo") é **revertida** por decisão do dono pós-piloto.
- Sem migration, sem UI, sem settings novos (`INTRANET_RESTRICTED_ROLES` e
  `INTRANET_IP_RANGE` mantêm semântica e defaults).

## Impact

- **Specs**: `account-access` (1 requisito MODIFIED, 4 cenários).
- **Código**: `apps/accounts/middleware.py` (1 ponto de decisão),
  `docs/adr/ADR-0002-…md` (seção de revisão).
- **Testes**: `apps/accounts/tests/test_intranet_guard.py` (cenário antigo da
  troca de papel reescrito; 2+ cenários novos).
- **Risco**: baixo — lógica central em um único middleware, bem coberto;
  caminho feliz (intranet, papéis não restritos) não muda e não ganha query
  nova (a consulta ao conjunto só ocorre no caminho que bloquearia).
