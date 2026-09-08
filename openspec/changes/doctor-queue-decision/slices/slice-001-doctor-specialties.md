# Slice 001: DoctorSpecialty + User.specialties

## Objetivo

Modelar os subtipos de médico (`angio|neuro|cardio|radio`) como model M2M
code-first semeado do catálogo, com `User.specialties` (vazio = generalista)
e helper puro — fundação do access control e do filtro de fila dos slices
seguintes.

## Contexto necessário

- `apps/cases/procedure_catalog.py` — `VALID_DOCTOR_SUBTYPES` (fonte única) e
  `ProcedureProfile.doctor_subtipo` por tipo.
- `apps/accounts/models.py` — padrão `Role` (model com `name` único + M2M
  `User.roles`) a espelhar; `seed_admin` NÃO muda (admin acessa tudo pelo
  papel, sem specialty).
- `apps/accounts/migrations/` — migrations existentes (o seed de specialties é
  **data migration** dentro da migration nova; padrão das seeds de `Role`).
- Design D1 (`openspec/changes/doctor-queue-decision/design.md`).

## Requisitos verificáveis

- **R1** `DoctorSpecialty(name unique)` em `apps/accounts/models.py`, com
  `__str__`; `User.specialties = M2M(DoctorSpecialty, blank=True,
  related_name="users")`.
- **R2** Migration única em `apps/accounts` cria model + M2M e semeia
  exatamente os 4 subtipos de `VALID_DOCTOR_SUBTYPES` (data migration lendo o
  catálogo — sem strings duplicadas).
- **R3** Helper puro `user_doctor_subtypes(user) -> set[str]`: subtipos do
  usuário (`set()` para generalista; usuários anônimos/inativos não ocorrem —
  helper assume User autenticado). Sem I/O de rede.
- **R4** Django admin expõe `specialties` no `UserAdmin` existente (atribuição
  manual, sem UI self-service).
- **R5** Testes: seed completo (4, sem duplicar em re-run da migration),
  generalista = conjunto vazio, M2M em ambos os lados, helper com 1 e N
  subtipos.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/accounts/models.py` | `test_specialty_model` |
| R2 | `apps/accounts/migrations/000X_doctor_specialty.py` | `test_seed_creates_four_subtypes`; `makemigrations --check` |
| R3 | `apps/accounts/subtypes.py` | `test_helper_*` (generalista/1/N) |
| R4 | `apps/accounts/admin.py` | inspeção + `test_admin_exposes_specialties` |
| R5 | `apps/accounts/tests/test_specialties.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/models.py
  - apps/accounts/subtypes.py            # helper puro (novo)
  - apps/accounts/admin.py
  - apps/accounts/migrations/000X_doctor_specialty.py
  - apps/accounts/tests/test_specialties.py

out_of_scope:
  - UI de fila/detalhe/decisão (slices 002–004)
  - mudanças em apps/cases (o catálogo é somente-leitura aqui)
  - seed_admin / autenticação / login
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/test_specialties.py`
- Falha esperada: `ImportError: cannot import name 'user_doctor_subtypes'`
  (módulo `apps.accounts.subtypes` ainda não existe).

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/accounts/tests/` — exit 0
  (regressão do app: auth/roles intactos).
- `uv run ruff check apps/accounts && uv run ruff format --check apps/accounts`
- `uv run mypy apps/accounts`
- `uv run python manage.py makemigrations --check --dry-run` — "No changes
  detected" (model ↔ migration coerentes).

## Critérios de aceitação

- [ ] R1–R5 comprovados; seed lê `VALID_DOCTOR_SUBTYPES` (sem duplicação de
      constantes) e é idempotente
- [ ] Nenhum teste existente quebrado no app accounts
- [ ] Gate parcial do slice verde
