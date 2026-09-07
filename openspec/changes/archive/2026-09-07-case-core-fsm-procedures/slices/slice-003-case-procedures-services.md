# Slice 003: CaseProcedure + serviços de procedimentos

## Objetivo

A dimensão de procedimento, neutra (padrão ADR-0004 do ats-web): model `CaseProcedure` com unicidade caso+tipo e os serviços atômicos de declaração (NIR), detecção (pipeline) e decisão médica por procedimento — cada operação substitui coerentemente o estado anterior, valida tipos contra o catálogo e grava evento.

## Contexto necessário (contexto zero)

- Slices 001–002 entregues: catálogo (`procedure_catalog.py`, fail-fast) e `Case`+FSM+`CaseEvent`.
- Referência (somente-leitura): `/projects/dev/ats-web/apps/cases/procedures.py` (`set_declared_procedures`, `set_detected_procedures`, `record_doctor_procedure_decisions`, `reset_detection_and_doctor_statuses`, leitores `get_declared/...`) e `apps/cases/models.py::CaseProcedure` (constraint `uniq_case_procedure_type`, `DetectionStatus`, `DoctorDisposition`). **Divergências deliberadas**: assinaturas HMD recebem `role` explícito (auditoria) e usam dicts tipados; `doctor_decided_at` é acréscimo HMD (ats-web não tem); `reset_detection_and_doctor_statuses` fica para o change 04 (reprocessamento).
- Design: `../design.md` D2 (Case neutro; fields por row), D5 (eventos).
- Spec: `../specs/case-management/spec.md` — Requirements "Procedimentos por caso neutros e atômicos" (3 cenários) e "Trilha de auditoria" (cenário de detecção).

## Requisitos

- **R1** `CaseProcedure`: `case FK CASCADE`, `procedure_type` (validado contra o catálogo na `clean()` — tipo fora do catálogo rejeitado), `declared_by_nir bool default False`, `detection_status ∈ {pending, detected, not_detected} default pending`, `doctor_disposition ∈ {pending, approved, denied} default pending`, `doctor_reason` (blank), `doctor_decided_at` (null), `created_at/updated_at`; `UniqueConstraint(case, procedure_type)`; migration.
- **R2** `set_declared_procedures(case, procedure_types, *, user, role)`: atômico — cria/ativa rows declaradas, desativa rows não declaradas; tipo inválido → `ValueError` nomeando o tipo e **nenhuma row criada** (rollback); grava evento `CASE_PROCEDURES_DECLARED` com a lista no payload.
- **R3** `set_detected_procedures(case, detection: dict[tipo, detected|not_detected], *, user, role)`: atualiza `detection_status` apenas de rows existentes; tipo sem row → erro explícito (pipeline deve declarar/reconciliar antes); grava evento com o mapa de detecção.
- **R4** `record_doctor_procedure_decisions(case, decisions: dict[tipo, (approved|denied, reason)], *, user, role)`: atualiza `doctor_disposition/doctor_reason/doctor_decided_at` por row; tipo sem row → erro; grava evento resumindo as decisões; **dispara a transição FSM de decisão** do slice 002 (`DOCTOR_DENIED|DOCTOR_ACCEPTED`, encadeando `request_scheduling` quando ≥1 aprovado) na mesma transação.
- **R5** Leitores: `get_declared_procedure_types`, `get_detected_procedure_types`, `selection_key` (string canônica ordenada dos tipos declarados) e `format_procedure_selection` (labels legíveis).
- **R6** Testes: declaração substitui anterior sem duplicatas nem órfãs; declaração com tipo inválido falha inteira nomeando o tipo; detecção atualiza e eventa; decisão por row com motivo + transição FSM correta (all denied → DOCTOR_DENIED→FINAL_REPLY_POSTED path; ≥1 approved → DOCTOR_ACCEPTED→SCHEDULER_REQUESTED); leitores; constraint de unicidade.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/cases/models.py`, `migrations/000X_caseprocedure.py` | `test_procedures.py::test_unique_constraint` |
| R2 | `apps/cases/procedures.py` | `::test_declaration_atomic_replace`, `::test_declaration_invalid_type_rolls_back` |
| R3 | `apps/cases/procedures.py` | `::test_detection_updates_and_events`, `::test_detection_unknown_row_fails` |
| R4 | `apps/cases/procedures.py` | `::test_decision_all_denied_transitions`, `::test_decision_partial_accept_transitions` |
| R5 | `apps/cases/procedures.py` | `::test_readers_and_selection_key` |
| R6 | `apps/cases/tests/test_procedures.py` | `uv run pytest apps/cases/tests/test_procedures.py` |

## RED

- Comando: `uv run pytest apps/cases/tests/test_procedures.py`
- Falha esperada: `ImportError` — `procedures.py`/`CaseProcedure` não existem.

## GREEN / verificação local

- `uv run pytest apps/cases/tests/test_procedures.py` — exit 0
- `uv run pytest apps/cases/tests/` — exit 0 (regressão FSM/catálogo)
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/models.py            # + CaseProcedure
  - apps/cases/procedures.py
  - apps/cases/migrations/000X_caseprocedure.py
  - apps/cases/tests/test_procedures.py
allowed_incidental_files: []
out_of_scope:
  - reconciliação com detecção de escopo LLM (change 06)
  - UI de declaração/decisão (changes 04/07)
  - prior-case (change 06)
  - qualquer mudança nas transições FSM existentes além do encadeamento previsto em R4
```

Escale ao parent se: o encadeamento R4 exigir nova transição FSM não prevista; precisar tocar o catálogo.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz (3 cenários da spec de procedimentos + cenário de evento de detecção)
- [ ] Toda operação atômica: falha no meio = zero efeito
- [ ] Nenhum tipo fora do catálogo persiste
- [ ] Gate parcial do slice verde
