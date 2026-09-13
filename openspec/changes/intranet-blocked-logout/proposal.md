# Change: intranet-blocked-logout

## Why

Armarduim real do piloto: um usuário NIR puro acessando de FORA do hospital é
bloqueado pelo guard, mas a sessão continua viva (cookie). A página de bloqueio
é texto puro (403 sem navbar): não há como disparar o logout (POST-only),
`/login/` redireciona autenticados de volta pra home (bloqueada) — o usuário
fica **preso** até limpar cookies do browser ou voltar à rede do hospital
(decisão do dono, 2026-09-13).

Com a regra vigente (conjunto de papéis, change `intranet-any-role-egress`),
bloqueado externamente = usuário cujos papéis são TODOS restritos (`nir`
puro) — para ele a sessão não tem valor fora da intranet, logo **encerrá-la é
seguro** e reduz a exposição (cookie de sessão vivo em rede externa). Sob a
regra antiga (papel ativo) essa solução seria errada; agora é a correta.

## What Changes

- No ramo de bloqueio do `IntranetGuardMiddleware`: `logout(request)` ANTES de
  responder (a sessão é encerrada; o SessionMiddleware derruba o cookie na
  volta da resposta — ordem do stack já garante).
- A resposta passa a renderizar o template `accounts/intranet_blocked.html`
  (HTTP 403 preservado) com a mensagem atual e **botão "Voltar ao login"**
  (`/login/` — isento do guard e, com o usuário anônimo, renderiza o form).
- Log de auditoria do evento ganha `session_terminated=1` (pk/role/ip/path;
  sem PII).
- Spec `account-access` MODIFIED: cenário "NIR puro fora da faixa é bloqueado"
  passa a incluir sessão encerrada + retorno ao login; novo cenário para o
  fluxo completo (bloqueado → anônimo → `/login/` acessível).

## Impact

- **Specs**: `account-access` (1 MODIFIED; 4 cenários carregados, 1 reescrito,
  1 novo).
- **Código**: `apps/accounts/middleware.py`, template novo
  `templates/accounts/intranet_blocked.html`, testes.
- **Risco**: baixo — caminho raro (só bloqueio), logout só para quem nunca
  acessa externo, fluxo pós-bloqueio determinístico (relogin externo → mesma
  página, sem loop).
