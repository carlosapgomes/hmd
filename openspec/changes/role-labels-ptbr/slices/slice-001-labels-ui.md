# Slice 001 — Fonte única de rótulos + 4 superfícies

## Goal

Implementar `role_label`/`ROLE_LABELS` (D1) e aplicar o filtro nas 4
superfícies (D2), com testes de renderização não-vacuos e migração dos
asserts antigos.

## Deliverables

### R1 — Fonte única (`apps/accounts/role_labels.py`)

- `ROLE_LABELS` com os 5 mapeamentos LITERAIS (inclui `nir`/`admin`
  explícitos) e `role_label(value) -> str` com fallback = própria chave (D3).
- Zero outra tabela de tradução de papel no repo (grep: nenhuma duplicata).

### R2 — Filtro (`apps/accounts/templatetags/role_labels.py`)

- `register.filter("role_label", role_label)` reusando a fonte (sem lógica
  própria); `apps/accounts/templatetags/__init__.py` criado se necessário.

### R3 — 4 superfícies

- `templates/base.html` (badge ativo): `{{ active_role|role_label }}`.
- `templates/accounts/home.html` (badges): `{{ name|role_label }}`.
- `templates/accounts/switch_role.html`: botão `{{ role|role_label }}`;
  `value="{{ role }}"` INTOCADO (chave no wire — D2).
- `templates/accounts/profile.html`: lista com rótulos (loop com filtro no
  lugar do `join` sobre chaves cruas).

### R4 — Testes (`apps/accounts/tests/test_role_labels.py` novo + migrações)

Novos (TDD, RED primeiro):
- Unit: mapeamento completo (5 entradas) + fallback de chave desconhecida
  (não-vacuidade: mutação do dicionário quebra o teste).
- Badge: login doctor → qualquer página autenticada exibe ">médico<" e NÃO
  exibe ">doctor<".
- Seleção: usuário doctor+manager → botões ">médico<" e ">supervisor<" E
  `value="doctor"`/`value="manager"` no HTML (chave no wire).
- Perfil/home: scheduler+manager → "agendador"/"supervisor" visíveis.
- E2E: clicar em "supervisor" (POST da chave) → sessão `active_role ==
  "manager"`.

Migrações (mudam de chave→rótulo SÓ nos asserts de HTML; asserts de
sessão/contexto ficam nas chaves):
- `apps/accounts/tests/test_active_role.py:90-91,155-157,323` (páginas de
  seleção/home: esperam rótulos).
- `apps/accounts/tests/test_auth_flow.py:91` (perfil de usuário doctor).
- Audit grep por `assert "doctor" in\|"manager" in body` nas páginas afetadas
  para não deixar assert vacuo/quebrado para trás.

## Out of Scope

- Manual (`templates/accounts/manual.html`) cita os papéis? Se citar as três
  palavras renomeadas em texto de UI, trocar (edits mecânicos permitidos);
  NÃO reescrever seções.
- `nir`/`admin`: sem mudança de exibição.
- Qualquer outra superfície: filas, dashboards, e-mails, logs (logs/chaves
  ficam inglês).

## Verification

```bash
TEST_DB_PORT=55435 uv run pytest apps/accounts -q          # RED→GREEN (mesmo comando)
TEST_DB_PORT=55435 uv run pytest -q                         # suíte completa
uv run ruff check . && uv run ruff format --check . && uv run mypy .
grep -rn "ROLE_LABELS" apps/ | grep -v role_labels.py       # sem duplicatas
```

## Expected files

- apps/accounts/role_labels.py (novo)
- apps/accounts/templatetags/__init__.py (novo, se inexistente)
- apps/accounts/templatetags/role_labels.py (novo)
- apps/accounts/tests/test_role_labels.py (novo)
- templates/base.html
- templates/accounts/home.html
- templates/accounts/switch_role.html
- templates/accounts/profile.html
- apps/accounts/tests/test_active_role.py
- apps/accounts/tests/test_auth_flow.py
- allowed incidental: templates/accounts/manual.html (só trocas literais)

## Deviations / learnings

- (preenchido na execução)
