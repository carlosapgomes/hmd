# ADR-0002: Papéis fixos e intranet guard apenas para o papel ativo `nir`

## Status

Accepted

## Contexto

O HMD reutiliza os papéis do ats-web (`nir`, `doctor`, `scheduler`, `manager`,
`admin`). Usuários podem ter múltiplos papéis, com um **papel ativo por vez**
guardado na sessão. O acesso externo ao sistema passa por túnel Cloudflare; o
papel `nir` (regulação) deve operar apenas dentro da intranet do hospital. No
ats-web a restrição de intranet considera o *conjunto* de papéis do usuário
(`scheduler` também era restrito).

## Decisão

- Papéis fixos: `nir`, `doctor`, `scheduler`, `manager`, `admin`.
- Usuário multi-role com papel ativo único em sessão
  (`ActiveRoleMiddleware` + view de troca de papel + context processor),
  seguindo o padrão ats-web.
- **Intranet guard apenas para `nir`** — divergência deliberada vs ats-web:
  - a restrição segue o **papel ativo** da sessão, não o conjunto de papéis;
  - `INTRANET_RESTRICTED_ROLES` vira **setting por env** (default `["nir"]`),
    em vez de constante no código;
  - `scheduler` acessa pela internet (unidade 2 agenda via internet);
  - paths de login/logout/troca de papel (+ static/media) são isentos — a
    troca de papel é a rota de fuga para o usuário multi-role bloqueado com
    papel ativo restrito;
  - **`/admin/` deliberadamente NÃO é isento** (esclarecimento registrado
    na revisão final do change 01, 2026-09-07): a restrição segue a função
    exercida — quem atua como `nir` não acessa nada externamente, inclusive
    o Django admin; superusuários multi-role têm a fuga via `/switch-role/`.
    Isentar o admin abriria bypass para `nir`-only externo, e nenhum fluxo
    do HMD exige isso.

## Alternativas Consideradas

1. **Restrição pelo conjunto de papéis (padrão ats-web)** — rejeitada: a
   pessoa bloqueada estaria exercendo a função `nir` no momento; a decisão
   deve seguir a função exercida.
2. **Restrição por papel como campo no User** — rejeitada: conflita com
   multi-role simultâneo e com sessões múltiplas do mesmo usuário.

## Consequências

- Positivas:
  - Semântica clara: a restrição acompanha o papel exercido na sessão.
  - Configurável por ambiente sem mudança de código.
- Negativas/Trade-offs:
  - Diferença de comportamento documentada vs ats-web; testes devem cobrir o
    cenário multi-role com papel ativo restrito e a rota de fuga via
    switch-role.
