# Slice 002: Case + FSM de 17 estados + CaseEvent

## Objetivo

O coração do domínio: `Case` (núcleo enxuto) com máquina de estados de 17 estados e transições protegidas, gravando `CaseEvent` append-only em cada transição. Comportamento observável: caminho feliz `NEW → … → AWAITING_DOCTOR`, negação médica → `FINAL_REPLY_POSTED` via `DOCTOR_DENIED`, aceitação → `SCHEDULER_REQUESTED` via `DOCTOR_ACCEPTED`, falha de processamento → `FAILED` com motivo na trilha, transição inválida rejeitada sem efeito.

## Contexto necessário (contexto zero)

- **Pré-condição**: dono confirmou D1 (`../design.md`) — biblioteca `django-fsm-2==4.2.4` (recomendada, MIT) ou `django-viewflow` (AGPL). Os requisitos abaixo são agnósticos; a diferença é a mecânica (decoradores/signals vs flow class/hooks).
- Design: `../design.md` D1 (lib), D4 (17 estados + transições + DOCTOR_ACCEPTED transitório-observável), D5 (CaseEvent), D7 (Case enxuto).
- Spec: `../specs/case-management/spec.md` — Requirements "FSM de 17 estados com transições protegidas" (5 cenários) e "Trilha de auditoria append-only" (2 cenários).
- Referência (somente-leitura): `/projects/dev/ats-web/apps/cases/models.py` — `CaseStatus` (TextChoices), `Case` com `FSMField(protected=True)` + `@transition` + `_record_event` (pending-event), `CaseEvent`. **Se D1=django-fsm-2**: padrão vale ~1:1 (renomear estados conforme design D4). **Se D1=viewflow**: adaptar para flow class + getter/setter + `on_success` (ver `temp/research/viewflow-fsm.md` §"Integração Django"), mantendo a mesma semântica de spec.
- Estados (17): `NEW, PDF_EXTRACTING, ANONYMIZING, LLM_EXTRACTING, LLM_SUMMARIZING, AWAITING_DOCTOR, DOCTOR_DENIED, DOCTOR_ACCEPTED, SCHEDULER_REQUESTED, AWAITING_SCHEDULING, SCHEDULING_CONFIRMED, SCHEDULING_DENIED, FAILED, FINAL_REPLY_POSTED, AWAITING_NIR_ACK, CLEANING, CLEANED`.
- Slice 001 entregou `apps/cases` (catálogo, sem models).

## Requisitos

- **R1** `Case`: `case_id UUID pk`, `status` FSM com `CaseStatus.NEW` default (protegido contra atribuição direta), `created_by FK PROTECT`, `agency_record_number` (blank) + `agency_record_extracted_at` (null), `created_at/updated_at`. Migration inicial do app.
- **R2** Transições de domínio com guards de source (nomes do design D4): cadeia de processamento (`start/complete` de pdf/anonimização/llm1/llm2), `fail_processing` (de qualquer estado de processamento → `FAILED`, recebe motivo), decisão médica (`AWAITING_DOCTOR → DOCTOR_DENIED|DOCTOR_ACCEPTED`), `request_scheduling` (`DOCTOR_ACCEPTED → SCHEDULER_REQUESTED`), `await_scheduling_confirmation`, `confirm_scheduling|deny_scheduling` (`AWAITING_SCHEDULING → SCHEDULING_CONFIRMED|SCHEDULING_DENIED`), `post_final_reply` (`DOCTOR_DENIED|SCHEDULING_CONFIRMED|SCHEDULING_DENIED → FINAL_REPLY_POSTED`), `nir_acknowledge`, `start_cleaning`, `complete_cleaning`.
- **R3** Transição inválida (operação cujo source não inclui o estado atual) → exceção da lib (`TransitionNotAllowed` ou equivalente), **sem** alterar status nem gravar evento.
- **R4** `CaseEvent`: `case FK CASCADE`, `timestamp` indexado, `actor_type ∈ {user, system}`, `actor FK SET_NULL null`, `actor_role`, `event_type` indexado, `payload JSON` — append-only (sem métodos de alteração; ordering por timestamp).
- **R5** Cada transição grava exatamente 1 evento (`CASE_STATUS_<DIREÇÃO>` ou tipo canônico equivalente) com ator (usuário informado ou `system`), papel ativo quando houver, e payload `{source, target, ...}`; `fail_processing` inclui o motivo no payload. Aceitação médica grava evento em `DOCTOR_ACCEPTED` **e** em `SCHEDULER_REQUESTED` (avanço na mesma transação via serviço de decisão — neste slice, método de caso que encadeia as duas transições atomicamente).
- **R6** Testes: caminho feliz completo até `AWAITING_DOCTOR` (sequência de completes); negação → `DOCTOR_DENIED → FINAL_REPLY_POSTED`; aceitação → `DOCTOR_ACCEPTED → SCHEDULER_REQUESTED → AWAITING_SCHEDULING → SCHEDULING_CONFIRMED → FINAL_REPLY_POSTED → AWAITING_NIR_ACK → CLEANING → CLEANED`; transição inválida rejeitada + estado preservado + sem evento; `fail_processing` de `ANONYMIZING` → `FAILED` + motivo no evento; evento por transição com ator/papel/payload (cenários da spec).
- **R7** `pyproject.toml` + `uv.lock` com a lib de D1 pinada; INSTALLED_APPS e settings mínimos (nenhum worker).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1/R2 | `apps/cases/models.py`, `apps/cases/migrations/0001_initial.py` | `test_fsm.py::test_happy_path_to_awaiting_doctor` |
| R3 | `apps/cases/models.py` | `test_fsm.py::test_invalid_transition_rejected` |
| R4 | `apps/cases/models.py` | `test_fsm.py::test_event_recorded_with_actor_and_role` |
| R5 | `apps/cases/models.py` (+services/mixin conforme lib) | `test_fsm.py::test_fail_processing_records_reason`, `::test_acceptance_chain_events` |
| R6 | `apps/cases/tests/test_fsm.py` | `uv run pytest apps/cases/tests/test_fsm.py` |
| R7 | `pyproject.toml`, `uv.lock` | `rg -n "django-fsm-2\|django-viewflow" pyproject.toml` |

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
  - apps/cases/migrations/0001_initial.py
  - apps/cases/tests/test_fsm.py
  - pyproject.toml
  - uv.lock
allowed_incidental_files:
  - apps/cases/fsm.py (flow class, se D1=viewflow)
  - apps/cases/tests/conftest.py (fixtures de usuário/papel)
out_of_scope:
  - CaseProcedure/serviços de procedimento (slice 003) — a decisão médica deste slice é de ESTADO (transição), sem rows
  - locks (slice 004), comunicações (slice 005)
  - campos pdf/estruturados/decisão/agendamento (changes 04–08; Case fica enxuto, design D7)
  - UI/admin
```

Escale ao parent se: a lib exigir adaptação além do previsto (ex.: protected quebra `refresh_from_db` no viewflow — ver pesquisa); algum estado/transição da fonte parecer inconsistente com a spec.

## Critérios de aceitação

- [ ] R1–R7 comprovados pelos comandos da matriz (5 cenários FSM + 2 de evento da spec cobertos)
- [ ] Status protegido contra atribuição direta
- [ ] Transição inválida: zero efeito (estado + trilha preservados)
- [ ] Gate parcial do slice verde
