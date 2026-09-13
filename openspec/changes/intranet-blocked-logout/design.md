# Design — intranet-blocked-logout

## D1 — Logout no bloqueio é seguro AGORA (e só agora)

O guard bloqueia externamente apenas usuários cujo conjunto de papéis ⊆
`INTRANET_RESTRICTED_ROLES` (regra do change `intranet-any-role-egress`):
para eles não existe fluxo externo válido — encerrar a sessão não remove
nenhuma capacidade. A decisão é deliberadamente condicionada a essa regra
(se um dia o guard voltar a bloquear por papel ativo, o logout forçado deve
ser revisitado). Bônus de segurança: nenhum cookie de sessão do HMD fica
vivo numa rede externa após o bloqueio.

## D2 — Resposta: 403 com template próprio + botão

- Status **403 preservado** (semântica de bloqueio; cenário da spec mantém o
  formato "resposta de acesso bloqueado").
- Template `accounts/intranet_blocked.html` estende `base.html` (consistência
  visual; nav anônima é segura — mostra Entrar) com: título da mensagem atual
  (`INTRANET_BLOCKED_MESSAGE` permanece a fonte), explicação de uma linha
  ("acesse pela rede interna do hospital ou entre novamente de dentro dela")
  e botão-link para `{% url 'login' %}` ("Voltar ao login").
- `INTRANET_BLOCKED_MESSAGE` continua sendo a constante-fonte (o template a
  recebe via context; nenhum texto duplicado em HTML).
- O logout acontece ANTES do render: a página já é renderizada para um
  usuário anônimo (nav coerente), e o clique em "Voltar ao login" cai no form
  (`login_view` só redireciona autenticados).

## D3 — Cookie derrubado pela ordem natural do stack

`logout(request)` dentro do guard marca a sessão para flush; o
`SessionMiddleware` (ANTES do guard no `MIDDLEWARE`) aplica o `Set-Cookie` de
expiração na volta da resposta — mesmo caminho do `logout_view` normal. Nada
de manipulação manual de cookies.

## D4 — Fluxo pós-bloqueio determinístico

Usuário nir puro tenta logar de fora: credenciais válidas → login OK →
redirect home → guard bloqueia + encerra sessão → página com botão. Sem loop
infinito perceptível (uma iteração, com explicação clara), sem sessão
persistente. `switch-role`/login/logout seguem isentos (inalterados).

## D5 — Log

Linha atual ganha o fato novo:
`intranet_guard_blocked user=<pk> role=<ativo> ip=<ip> path=<path> session_terminated=1`
(pk, sem CPF/username — mesma política D5 do change anterior).
