# Slice 002: Fila médica com filtro de subtipo e access control

## Objetivo

Criar `apps/doctor` com a fila de casos por estado (`aguardando`/`decididos`)
e filtro por subtipo, sob access control fechado (matriz D2): médico vê o que
pode decidir; generalista/admin veem tudo; outros papéis 403.

## Contexto necessário

- `apps/accounts/decorators.py::role_required` (guard por papel ativo).
- `apps/accounts/subtypes.py::user_doctor_subtypes` (slice 001).
- `apps/cases/procedure_catalog.py` — `get_procedure_profile(type).doctor_subtipo`,
  `PROCEDURE_TYPES`/ordem canônica.
- `apps/cases/procedures.py::get_declared_procedure_types`.
- `apps/cases/models.py` — `CaseStatus` (`AWAITING_DOCTOR`, `DOCTOR_DENIED`,
  `DOCTOR_ACCEPTED`, `SCHEDULER_REQUESTED`), campos de exibição
  (`patient_name`, `agency_record_number`, `created_at`, `status`).
- `apps/intake/{views,urls}.py` + `templates/intake/my_cases.html` — padrão de
  lista paginada + badge + filtro por papel ativo a replicar (a fila NÃO usa
  ownership: é papel, não dono).
- `templates/base.html` — nav por `active_role` (adicionar bloco `doctor`).
- `config/urls.py` — `path("doctor/", include("apps.doctor.urls"))`.
- Design D2/D5 (`openspec/changes/doctor-queue-decision/design.md`).

## Requisitos verificáveis

- **R1** `apps/doctor` registrado (apps.py, `config/settings/base.py`,
  `config/urls.py` em `/doctor/`); nav no `base.html` visível quando
  `active_role in doctor,admin`.
- **R2** Guard `role_required("doctor", "admin")` na fila; outros papéis → 403
  (teste com cada papel ativo relevante: `nir`, `scheduler`, `manager`).
- **R3** Fila: aba por estado — `aguardando` (default) = `AWAITING_DOCTOR`;
  `decididos` = `DOCTOR_DENIED|DOCTOR_ACCEPTED|SCHEDULER_REQUESTED`; FIFO por
  `created_at`; paginada (padrão do projeto).
- **R4** Filtro de subtipo no querystring (`?subtype=`): `Todas` (default) +
  apenas subtipos **do usuário** (generalista sem subtipo vê `Todas` +
  qualquer subtipo; admin idem). Predicado: caso tem ≥1 tipo declarado cujo
  `doctor_subtipo == subtype`.
- **R5** Cada card lista tipos declarados com badge de subtipo + contagem de
  pendentes por subtipo no cabeçalho (para o filtro do usuário).
- **R6** Testes de view (cliente logado com papel ativo): matriz de acesso
  (R2), estados das abas (R3), filtro por subtipo inclui/exclui corretamente
  (R4), generalista vê tudo, admin vê tudo.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/doctor/{apps,urls,views}.py`, `config/{settings/base,urls}.py`, `templates/base.html`, `templates/doctor/queue.html` | `test_nav_visible_for_doctor` |
| R2 | `apps/doctor/views.py` | `test_queue_forbidden_*` (nir/scheduler/manager/anônimo) |
| R3 | `apps/doctor/views.py` | `test_queue_awaiting_default`, `test_queue_decided_tab` |
| R4 | `apps/doctor/views.py` (ou `services.py` puro) | `test_subtype_filter_*`, `test_generalist_sees_all`, `test_admin_sees_all` |
| R5 | `templates/doctor/queue.html` | `test_queue_shows_types_and_pending_counts` |
| R6 | `apps/doctor/tests/test_queue.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/doctor/{__init__,apps,urls,views}.py
  - apps/doctor/tests/{__init__,test_queue}.py
  - templates/doctor/queue.html
  - config/settings/base.py        # INSTALLED_APPS
  - config/urls.py
  - templates/base.html            # nav

out_of_scope:
  - detalhe do caso/presenter/decisão (slices 003–004)
  - mudanças em apps/cases (usar API existente)
  - estilos novos além do tema/Bootstrap existentes
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/doctor/tests/test_queue.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.doctor'`.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/doctor/tests/ apps/accounts/tests/`
  — exit 0.
- `uv run ruff check apps/doctor config && uv run ruff format --check apps/doctor config`
- `uv run mypy .`

## Critérios de aceitação

- [ ] R1–R6 comprovados; access control testado para TODOS os papéis ativos
- [ ] Predicado do filtro idêntico ao do access control (mesma função ou
      mesma fonte)
- [ ] Gate parcial do slice verde
