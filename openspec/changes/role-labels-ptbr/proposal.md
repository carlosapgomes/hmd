# Proposal: role-labels-ptbr

## Why

O piloto em produção mostra os nomes dos papéis em inglês na interface
(badge "Papel ativo", tela de seleção de papel, lista de papéis da home e do
perfil): `doctor`, `scheduler`, `manager`. Os usuários do hospital — leitura
clínica e operacional — esperam os termos em português.

## What Changes

- Nova fonte única de rótulos: `apps/accounts/role_labels.py` com
  `role_label(value)` e o mapeamento fixo
  `doctor → médico`, `scheduler → agendador`, `manager → supervisor`,
  `nir → nir`, `admin → admin`; chave desconhecida exibe a própria chave
  (fallback seguro).
- Template filter `{% load role_labels %}` + `{{ valor|role_label }}` aplicado
  às 4 superfícies: badge de papel ativo (`base.html`), badges da home
  (`accounts/home.html`), botões da seleção de papel
  (`accounts/switch_role.html` — o `value` submetido permanece a CHAVE crua) e
  lista do perfil (`accounts/profile.html`).
- Testes migrados: asserts de HTML que esperavam os nomes crus passam a
  esperar os rótulos; asserts de sessão/valor/permissionamento continuam nas
  chaves.

## Capabilities

### Modified: `account-access`

- ADDED requirement "Rótulos de papel em português na interface" com cenários
  para badge ativo, seleção multi-role, perfil e fallback de chave
  desconhecida.

## Impact

- Zero mudança de dados, sessão, permissionamento ou URLs: `Role.name`,
  `active_role` da sessão e todos os guards continuam usando as chaves
  inglesas; a tradução é SÓ na renderização.
- Prefiro mudanças mínimas: 1 slice, sem migrar unidades anteriores (UI de
  filas etc. não exibem nomes de papel além dessas 4 superfícies).
- Arquivos afetados: `apps/accounts/role_labels.py` (novo) +
  `apps/accounts/templatetags/__init__.py`/`role_labels.py` (novo) +
  4 templates + testes.

## Não-goais

- Não traduz termos clínicos, nomes de telas ou o manual (exceto onde o
  manual citar os três papéis renomeados).
- Não muda seed, AD, guard, dispatcher nem specs de permissionamento.
