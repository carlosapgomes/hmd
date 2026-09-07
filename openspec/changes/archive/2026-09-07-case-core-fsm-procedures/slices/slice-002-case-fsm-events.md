# Slice 002: Case + FSM de 17 estados + CaseEvent

## Objetivo

O coração do domínio: `Case` (núcleo enxuto) com máquina de estados de 17 estados e transições protegidas, gravando `CaseEvent` append-only em cada transição. Comportamento observável: caminho feliz `NEW → … → AWAITING_DOCTOR`, negação médica → `FINAL_REPLY_POSTED` via `DOCTOR_DENIED`, aceitação → `SCHEDULER_REQUESTED` via `DOCTOR_ACCEPTED`, falha de processamento → `FAILED` com motivo na trilha, transição inválida rejeitada sem efeito.

## Contexto necessário (contexto zero)

- **Biblioteca (D1 confirmada)**: `django-fsm-2==4.2.4` (MIT, API django-fsm preservada — `@transition`/`FSMField(protected=True)`/signals). Pesquisa: `temp/research/viewflow-fsm.md`.
- Design: `../design.md` D1 (lib), D4 (17 estados + transições + DOCTOR_ACCEPTED transitório-observável), D5 (CaseEvent), D7 (Case enxuto).
- Spec: `../specs/case-management/spec.md` — Requirements "FSM de 17 estados com transições protegidas" (5 cenários) e "Trilha de auditoria append-only" (2 cenários).
- Referência (somente-leitura): `/projects/dev/ats-web/apps/cases/models.py` — `CaseStatus` (TextChoices), `Case` com `FSMField(protected=True)` + `@transition` + `_record_event` (pending-event), `CaseEvent`. Com `django-fsm-2` o padrão vale ~1:1 (renomear estados conforme design D4).
- Estados (17): `NEW, PDF_EXTRACTING, ANONYMIZING, LLM_EXTRACTING, LLM_SUMMARIZING, AWAITING_DOCTOR, DOCTOR_DENIED, DOCTOR_ACCEPTED, SCHEDULER_REQUESTED, AWAITING_SCHEDULING, SCHEDULING_CONFIRMED, SCHEDULING_DENIED, FAILED, FINAL_REPLY_POSTED, AWAITING_NIR_ACK, CLEANING, CLEANED`.
- Slice 001 entregou `apps/cases` (catálogo, sem models).

## Requisitos

- **R1** `Case`: `case_id UUID pk`, `status` FSM com `CaseStatus.NEW` default (protegido contra atribuição direta), `created_by FK PROTECT`, `agency_record_number` (blank) + `agency_record_extracted_at` (null), `created_at/updated_at`. Migration inicial do app.
- **R2** Tabela completa de transições conforme design D4 (source → target por operação, incl. self-transitions de início de worker em `ANONYMIZING`/`LLM_EXTRACTING`/`LLM_SUMMARIZING` para registrar o início sem mudar estado, e target dinâmico da decisão médica). **Cada transição testada individualmente** (source válido progride; source inválido rejeita).
- **R3** Transição inválida (operação cujo source não inclui o estado atual) → exceção da lib (`TransitionNotAllowed` ou equivalente), **sem** alterar status nem gravar evento.
- **R4** `CaseEvent`: `case FK CASCADE`, `timestamp` indexado, `actor_type ∈ {user, system}` (divergência do ats-web, que usa `human`), `actor FK SET_NULL null`, **`actor_role` (acréscimo HMD — ats-web não tem)**, `event_type` indexado, `payload JSON` — append-only (sem métodos de alteração; ordering por timestamp). Tipos canônicos em `apps/cases/events.py` (enum único consumido por FSM/serviços/locks/projeção — design D5).
- **R5** **Gravação direta** (divergência do ats-web pending-event+signal — design D5): cada transição recebe `*, user=None, role=None`, faz `save()` e cria o `CaseEvent` **no mesmo `transaction.atomic()`**; `actor_role` é parâmetro explícito (views futuras extraem da sessão; workers passam `role="system"`). Evento de transição = `CASE_STATUS_<TARGET>` com payload `{source, target, ...}`; `fail_processing` inclui motivo. Aceitação médica grava evento em `DOCTOR_ACCEPTED` **e** em `SCHEDULER_REQUESTED` (encadeadas atomicamente).
- **R6** Testes: **cada transição da tabela D4 individualmente** (source válido progride + grava evento; source inválido rejeita + nada grava); caminhos completos (feliz até `AWAITING_DOCTOR`; negação → `DOCTOR_DENIED → FINAL_REPLY_POSTED`; aceitação → … → `CLEANED`); transição inválida preserva estado e trilha; `fail_processing` de cada estado de processamento → `FAILED` + motivo; eventos com ator/papel para papéis `nir`, `doctor` e `scheduler` (cenários da spec).
- **R7** `pyproject.toml` + `uv.lock` com a lib de D1 pinada; INSTALLED_APPS e settings mínimos (nenhum worker).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1/R2 | `apps/cases/models.py`, `apps/cases/migrations/0001_initial.py` | `test_fsm.py::test_happy_path_to_awaiting_doctor` |
| R3 | `apps/cases/models.py` | `test_fsm.py::test_invalid_transition_rejected` |
| R4 | `apps/cases/models.py` | `test_fsm.py::test_event_recorded_with_actor_and_role` |
| R5 | `apps/cases/models.py` (+services/mixin conforme lib) | `test_fsm.py::test_fail_processing_records_reason`, `::test_acceptance_chain_events` |
| R6 | `apps/cases/tests/test_fsm.py` | `uv run pytest apps/cases/tests/test_fsm.py` |
| R7 | `pyproject.toml`, `uv.lock` | `rg -n "django-fsm-2" pyproject.toml` |

## RED

- Comando: `uv run pytest apps/cases/tests/test_fsm.py`
- Falha esperada: `ImportError`/`AttributeError` — `Case`/`CaseStatus` não existem.

## GREEN / verificação local

- `uv run pytest apps/cases/tests/test_fsm.py` — exit 0
- `uv run pytest apps/cases/tests/` — exit 0 (regressão do catálogo)
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0
- Migration aplicável em banco limpo (pytest --reuse-db cria/aplica)

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/models.py
  - apps/cases/events.py
  - apps/cases/migrations/0001_initial.py
  - apps/cases/tests/test_fsm.py
  - docs/adr/ADR-0005-case-neutro-catalogo-code-first-fsm.md
  - pyproject.toml
  - uv.lock
allowed_incidental_files:
  - apps/cases/tests/conftest.py (fixtures de usuário/papel)
out_of_scope:
  - CaseProcedure/serviços de procedimento (slice 003) — a decisão médica deste slice é de ESTADO (transição), sem rows
  - locks (slice 004), comunicações (slice 005)
  - campos pdf/estruturados/decisão/agendamento (changes 04–08; Case fica enxuto, design D7)
  - UI/admin
```

Escale ao parent se: a lib exigir adaptação além do previsto; algum estado/transição da fonte parecer inconsistente com a spec.

## Critérios de aceitação

- [ ] R1–R7 comprovados pelos comandos da matriz (5 cenários FSM + 2 de evento da spec cobertos)
- [ ] Status protegido contra atribuição direta
- [ ] Transição inválida: zero efeito (estado + trilha preservados)
- [ ] Gate parcial do slice verde
