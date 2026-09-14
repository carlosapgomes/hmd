# Slice 001 — Fonte única de rótulos + 9 superfícies

## Contexto necessário

- Chaves de papel em `Role.name`/sessão/guards NÃO mudam; só a EXIBIÇÃO.
- `{% load role_labels %}` NÃO é herdado por filhos (D4): cada template que
  usar o filtro precisa do próprio load.
- Superfícies (linhas aproximadas, conferir no arquivo):
  - `templates/base.html:82` badge ativo `{{ active_role }}`
  - `templates/accounts/home.html:20` badges `{{ name }}`
  - `templates/accounts/switch_role.html:19` botões `{{ role }}`
    (`value="{{ role }}"` INTOCADO)
  - `templates/accounts/profile.html:27` `{{ user_roles|join:", " }}`
  - `templates/doctor/case_detail.html:85` "papel `{{ decision_event.actor_role }}`"
  - `templates/doctor/case_detail.html:317` trilha "papel `{{ event.actor_role }}`"
  - `templates/intake/case_detail.html:305` trilha "papel `{{ event.actor_role }}`"
  - `templates/intake/case_detail.html:341` badge `{{ communication.author_role }}`
  - `templates/scheduler/case_detail.html:248` badge `{{ communication.author_role }}`
- Mapeamento (dono): doctor→médico, scheduler→agendador, manager→supervisor,
  nir→nir, admin→admin; fallback = própria chave (`system`, `nurse`…).
- Templates com `APP_DIRS=True`, sem override de loaders — templatetags novo
  é descoberto sem config extra.

## Goal

Implementar `role_label`/`ROLE_LABELS` (D1) e aplicar o filtro nas 9
superfícies (D2), com testes não-vacuos e migração consciente dos asserts.

## Deliverables

### R1 — Fonte única (`apps/accounts/role_labels.py`)

- `ROLE_LABELS` com os 5 mapeamentos LITERAIS e `role_label(value) -> str`
  com fallback = própria chave (D3).
- Zero outra tabela de tradução de papel no repo (grep sem duplicatas).

### R2 — Filtro (`apps/accounts/templatetags/role_labels.py`)

- `register.filter("role_label", role_label)` reusando a fonte;
  `apps/accounts/templatetags/__init__.py` criado.

### R3 — 9 superfícies + loads

- Aplicar `{{ …|role_label }}` nos 9 pontos do "Contexto necessário".
- `{% load role_labels %}` em TODA template alterada (D4).
- `switch_role.html`: SOMENTE o texto do botão vira rótulo; `value="{{ role }}"`
  permanece a chave.

### R4 — Testes (`apps/accounts/tests/test_role_labels.py` novo + migrações)

Novos (TDD, RED primeiro):
- Unit: mapeamento completo (5 entradas) + fallback (`role_label("system") ==
  "system"`, `role_label("nurse") == "nurse"`) — não-vacuidade: mutação do
  dicionário quebra o teste.
- Badge: papel ativo `doctor` → página autenticada exibe `>médico<` no badge
  (badge-scoped, NÃO substring solta: comentários HTML de base.html contêm a
  chave crua e tornariam o assert vacuo).
- Seleção: usuário doctor+manager → botões `>médico<`/`>supervisor<` E
  `value="doctor"`/`value="manager"` no HTML (chave no wire).
- Perfil: scheduler+manager → "agendador" e "supervisor" na lista.
- Home da conta: papéis `nurse`+`doctor` com ativo `nurse` (fora da tabela de
  áreas, caso análogo ao `test_home_dispatch.py:86-107`) → lista exibe
  "nurse" (fallback) e ">médico<".
- Trilha/comunicações: caso com evento `doctor` e comunicação `manager` →
  "papel médico" na trilha de `doctor/case_detail` e badge ">supervisor<" na
  comunicação de `intake/case_detail` (uma visão basta por tipo de ponto,
  cobrindo os dois padrões `actor_role`/`author_role`).
- E2E: POST da chave do botão rotulado "supervisor" → sessão
  `active_role == "manager"`.

Migrações (linha a linha; asserts de sessão/contexto FICAM nas chaves):
- `apps/accounts/tests/test_active_role.py:90-91` — continuam verdes via
  `value="doctor"`/`value="manager"` dos hidden inputs; TROCAR para
  badge/botão-scoped com rótulos (`>médico<`) E manter asserts de value
  (evitar vacuidade por comentários HTML).
- `:155` — `assert "manager" in body` idem (botão-scoped); `:156`
  `assert "nurse" in body` DEIXA como está (fallback, botão mostra "nurse");
  `:157` negativo mantém (migrar alvo se apontar ao badge).
- `:201` `assert "manager" in home.content.decode()` — fica verde APENAS via
  comentário HTML (`base.html:45-49`): TROCAR para badge-scoped ">supervisor<"
  (a página é a home do dispatcher com ativo manager).
- `:322-323` e `apps/accounts/tests/test_auth_flow.py:91` — idem
  badge/lista-scoped (comentários `base.html:31-42` mantêm-nos verdes de
  graça; trocar para não-vacuos).
- Audit final: `grep -n "doctor\|scheduler\|manager" apps/*/tests/*.py` nas
  páginas renderizadas por esses testes — nenhum assert de HTML de role fica
  dependendo de substring solta no body.

## Out of Scope

- `templates/accounts/manual.html`: VERIFY-ONLY (já usa "Médico"/"Agendador"/
  "NIR" — nenhuma edição esperada; não mexer sem necessidade).
- `nir`/`admin`: sem mudança de exibição. Admin Django: chave crua (staff).
- Logs, e-mails, seeds, permissionamento, notificações.

## Matriz requisito → arquivo → teste

| Requisito/spec | Código | Teste |
|---|---|---|
| Fonte única + fallback | `apps/accounts/role_labels.py` | test_role_labels.py (unit) |
| Filtro | `apps/accounts/templatetags/role_labels.py` | via testes de UI |
| Badge ativo | `templates/base.html` | badge-scoped |
| Seleção (rótulo+chave) | `templates/accounts/switch_role.html` | rótulo E value |
| Home da conta | `templates/accounts/home.html` | fallback+>médico< |
| Perfil | `templates/accounts/profile.html` | agendador/supervisor |
| Trilha/decisão | `doctor/case_detail.html:85,317` | "papel médico" |
| Trilha intake | `intake/case_detail.html:305` | incluído no teste de trilha |
| Comunicações | `intake:341`/`scheduler:248` | badge ">supervisor<" |
| Troca por chave | — | E2E sessão |

## Verification (RED → GREEN, mesmo comando)

```bash
TEST_DB_PORT=55435 uv run pytest apps/accounts -q
TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_home_dispatch.py apps/accounts/tests/test_auth_flow.py -q
# GREEN total:
TEST_DB_PORT=55435 uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy .
grep -rn "ROLE_LABELS *=" apps/ | grep -v "apps/accounts/role_labels.py"  # → vazio (sem 2ª tabela)
```

## Expected files

- apps/accounts/role_labels.py (novo)
- apps/accounts/templatetags/__init__.py (novo)
- apps/accounts/templatetags/role_labels.py (novo)
- apps/accounts/tests/test_role_labels.py (novo)
- templates/base.html
- templates/accounts/home.html
- templates/accounts/switch_role.html
- templates/accounts/profile.html
- templates/doctor/case_detail.html
- templates/intake/case_detail.html
- templates/scheduler/case_detail.html
- apps/accounts/tests/test_active_role.py
- apps/accounts/tests/test_auth_flow.py

- allowed incidental files: NENHUM (manual é verify-only)

## Acceptance criteria

- 9 superfícies com rótulo; chave no wire/data inalterada (E2E verde).
- Nenhum assert de role em HTML dependendo de substring solta no body.
- Suíte completa verde; ruff/format/mypy limpos; grep de duplicatas vazio.
- Verificação e matriz acima preenchidas no relato.

## Deviations / learnings

- (preenchido na execução)
