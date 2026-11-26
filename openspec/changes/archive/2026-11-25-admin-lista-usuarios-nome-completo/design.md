# Design

## Context

- `User` (`apps/accounts/models.py`) herda `first_name`/`last_name` de
  `AbstractUser` (ambos opcionais) e já expõe `display_name`
  (`get_full_name()` ou `username`) — regra única já consumida pelo cabeçalho
  do app, pelos locks e pela trilha de eventos.
- `UserAdmin` (`apps/accounts/admin.py`) define
  `list_display = ("username", "ad_upn", "account_status", "is_staff")` e
  `search_fields = ("username", "ad_upn")`. Nome e sobrenome já são
  editáveis no formulário do usuário (fieldset «Personal info» herdado do
  `UserAdmin` do Django).
- Motivação: `proposal.md` — Why.

## Goals / Non-Goals

- Goal: o administrador reconhece quem é quem na lista sem abrir cada
  registro, e encontra usuários pelo nome.
- Non-goal: qualquer mudança de login, de modelo ou dos formulários.

## Decisões

### D1 — Coluna calculada que reusa `display_name`

Um método de coluna com `@admin.display(description="Nome completo")`
devolvendo `obj.display_name`. Alternativa considerada: duas colunas brutas
`first_name`/`last_name` — mostra «-» para quem não tem nome, ocupa duas
colunas e deixa o fallback por conta do leitor; rejeitada. Nenhuma lógica
nova: a regra «nome completo ou username» continua no modelo.

### D2 — Ordenação pelo primeiro nome

`ordering="first_name"` torna o cabeçalho clicável e ordena pelo primeiro
nome. Usuários sem primeiro nome ficam no início na ordem crescente (string
vazia); o fallback (username) não entra na chave de ordenação. Alternativa
considerada: coluna sem ordenação — perde utilidade; rejeitada. Limitação
aceita e de baixo impacto (base de usuários pequena).

### D3 — `username` continua sendo a primeira coluna e o link

`full_name` entra logo após `username` no `list_display`. O Django usa a
primeira coluna como link de edição por padrão; `list_display_links` não é
declarado (configuração redundante). Alternativa considerada: nome primeiro
mais `list_display_links = ("username",)` — rejeitada por trocar a ordem de
leitura que o admin já conhece (login é a identidade operacional).

### D4 — Busca por nome e sobrenome

`search_fields` ganha `first_name` e `last_name` (lookup padrão `icontains`).
Sem exposição nova: nomes já aparecem no formulário de edição e a lista é
superfície de administrador.

### D5 — Rótulo em português pela coluna, sem migration

O rótulo «Nome completo» vem do `description` da coluna; `verbose_name` dos
campos não é alterado (geraria migration por ganho cosmético).

### D6 — Testes tipados no molde do app

Teste novo em `apps/accounts/tests/` com `admin_client` (padrão de
`test_ad_upn.py`/`test_admin_professional_council.py`): asserção da
estrutura (`list_display`/`search_fields`), renderização da lista (coluna,
fallback, link no username), ordenação e busca. Tipados para o mypy strict.

## Riscos e trade-offs

- **Ordem de quem não tem nome** (D2): ficam agrupados no início → aceito;
  a coluna mostra o username nesses casos.
- **Nome da coluna acoplado ao método** (`field-full_name` no HTML): é parte
  da configuração do admin; mudar o nome exige ajustar o teste.
- **Tamanho das respostas da lista** muda: testes existentes assertam
  substrings (`username`, UPN), não tamanho — sem impacto.
- **Sem impacto** em autenticação, lockout, formulários ou permissões.

## Migration Plan

Sem migration e sem dados. Entra na próxima release; rollback é reverter o
commit.
