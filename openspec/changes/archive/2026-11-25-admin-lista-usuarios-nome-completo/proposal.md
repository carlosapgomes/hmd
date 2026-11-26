# Proposal

## Why

A lista de usuários do Django admin mostra apenas `username`, `ad_upn`,
`account_status` e `is_staff`. Como o login do HMD é o `username` (o CPF, por
desenho) e não o UPN, o administrador precisa conferir qual login cadastrou
para cada pessoa — e hoje só consegue abrindo registro por registro. Em
25/11/2026 dois usuários AD foram provisionados com `username` de nome real e
não conseguiram entrar digitando o CPF; a lista não ajudou a perceber isso.

## What Changes

- Coluna «Nome completo» na lista de usuários (`UserAdmin.list_display`),
  com o nome do usuário e fallback para o `username` quando o nome está
  vazio; ordenável pelo primeiro nome.
- A busca da lista (`search_fields`) passa a casar também com nome e
  sobrenome, além de `username` e `ad_upn`.
- `username` permanece a primeira coluna (link de edição da lista).

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

- `account-access`: novos requisitos «Lista de usuários exibe o nome
  completo» e «Busca da lista de usuários alcança nome e sobrenome».

## Non-goals

- Mudar a semântica do login (continua sendo o `username`) ou renomear
  usuários existentes — decisão operacional do administrador.
- Colunas extras (e-mail, papéis, último login) ou filtro lateral.
- `verbose_name` em português para `first_name`/`last_name` (exigiria
  migration); o rótulo é resolvido pela coluna calculada.
- Alterar `User.__str__` (afetaria log de ações e selects do admin).

## Impact

- `apps/accounts/admin.py` (`UserAdmin`: `list_display`, `search_fields`,
  um método de coluna) e teste novo em `apps/accounts/tests/`.
- Sem migration, sem mudança de modelo, sem toque no fluxo de
  autenticação (backends/Kerberos/anti-lockout) nem nos formulários do admin.
