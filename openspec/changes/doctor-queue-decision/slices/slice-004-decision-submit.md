# Slice 004: Decisão por procedimento + caso decidido

## Objetivo

Fechar o estágio médico: formulário de decisão por procedimento com motivo
obrigatório em negativas, submit atômico sobre o serviço do change 03 (rows +
evento + transição FSM encadeada), tratamento de submissão concorrente e
detalhe read-only do caso decidido.

## Contexto necessário

- `apps/cases/procedures.py::record_doctor_procedure_decisions(case,
  decisions: Mapping[str, tuple[str, str]], *, user, role)` — **serviço
  existente do change 03**: valida tipos/rows, grava rows + evento
  `CASE_DOCTOR_DECISIONS_RECORDED` e transiciona (`DOCTOR_DENIED` ou
  `DOCTOR_ACCEPTED` + `request_scheduling` encadeado) no mesmo atomic.
  NÃO modificar este serviço.
- `apps/cases/models.py` — `CaseStatus`, `DoctorDisposition`
  (`pending|approved|denied`); transição fora de `AWAITING_DOCTOR` levanta
  `TransitionNotAllowed` (django_fsm_2).
- `apps/doctor/presenters.py` + `views.py` dos slices 002–003 (guard,
  `can_access_case`, contexto do presenter — estender, não duplicar).
- ats-web `apps/doctor/forms.py::DoctorDecisionForm` (padrão de campos
  dinâmicos por procedimento — somente-leitura).
- Design D4/D6 (`openspec/changes/doctor-queue-decision/design.md`).

## Requisitos verificáveis

- **R1** `DoctorDecisionForm` dinâmico sobre os tipos **declarados**: para
  cada tipo, `procedure_<type>__disposition` (choices
  `approved|denied`, default vazio obrigatório) e
  `procedure_<type>__reason` (textarea); validação: motivo obrigatório quando
  `denied` (erro de campo nomeando o procedimento); todos os procedimentos
  precisam de decisão (não há "sem decisão").
- **R2** `GET doctor:case_decide` (`/doctor/case/<id>/decide/`): presenter
  (slice 003) + formulário; apenas em `AWAITING_DOCTOR` (caso já decidido →
  redirect ao detalhe com mensagem); guard completo (papel + subtipo).
- **R3** `POST doctor:case_decide`: access control reforcado **na view**
  (403 antes de qualquer escrita); form válido →
  `record_doctor_procedure_decisions(user=request.user,
  role=active_role)` → redirect ao detalhe com flash "decisão registrada"
  (caso foi para negado ou para a fila de agendamento).
- **R4** Submissão concorrente/estado inválido (`TransitionNotAllowed` ou
  caso fora de `AWAITING_DOCTOR` no POST): nenhuma escrita parcial (serviço é
  atomic), mensagem clara ("caso já decidido por outro médico") e redirect ao
  detalhe — HTTP 4xx/redirect, nunca 500.
- **R5** Detalhe read-only pós-decisão: presenter estendido exibe decisões
  por procedimento (disposição + motivo + `doctor_decided_at`), ator e data
  (do evento `CASE_DOCTOR_DECISIONS_RECORDED`) e trilha de eventos do caso;
  sem formulário fora de `AWAITING_DOCTOR`.
- **R6** Testes: form (motivo obrigatório por procedimento nomeado; aprovado
  sem motivo ok), POST feliz negado-total → `DOCTOR_DENIED` com rows/eventos,
  POST misto → `SCHEDULER_REQUESTED`, POST concorrente → sem efeito parcial +
  sem 500, guard por subtipo no POST (403), detalhe decidido read-only.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/doctor/forms.py` | `test_form_requires_reason_on_deny`, `test_form_all_missing_rejected` |
| R2 | `apps/doctor/views.py` | `test_decide_get_renders_form`, `test_decide_get_redirects_when_decided` |
| R3 | `apps/doctor/views.py` | `test_submit_denied_all`, `test_submit_mixed_accepts` |
| R4 | `apps/doctor/views.py` | `test_concurrent_submit_no_partial_write`, `test_submit_wrong_state_no_500` |
| R5 | `apps/doctor/presenters.py`, `templates/doctor/case_detail.html` | `test_decided_detail_readonly` |
| R6 | `apps/doctor/tests/test_decision.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/doctor/forms.py
  - apps/doctor/views.py            # estende detail/decide
  - apps/doctor/urls.py
  - apps/doctor/presenters.py       # seção de decisões no contexto
  - apps/doctor/tests/test_decision.py
  - templates/doctor/{case_detail,decide}.html

out_of_scope:
  - `post_final_reply`/resposta ao NIR (change 09) — negado PERMANECE em
    DOCTOR_DENIED após este change
  - fila/validações de agendamento (change 08)
  - mudanças em apps/cases (serviço do change 03 é consumido como está)
  - mensageria/notificações (change 11)
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/doctor/tests/test_decision.py`
- Falha esperada: `ImportError: cannot import name 'DoctorDecisionForm'`.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/doctor/tests/` — exit 0.
- `TEST_DB_PORT=55435 uv run pytest apps/doctor/tests/ apps/cases/tests/`
  (regressão do domínio consumido) — exit 0.
- `uv run ruff check apps/doctor && uv run ruff format --check apps/doctor`
- `uv run mypy .`

## Critérios de aceitação

- [ ] R1–R6 comprovados; nenhuma escrita fora do atomic do serviço; POST com
      subtipo sem acesso → 403 SEM escrita
- [ ] Estados pós-decisão (`DOCTOR_DENIED`, `SCHEDULER_REQUESTED`) exibidos
      na aba "decididos" do slice 002 sem alteração deste
- [ ] Gate parcial do slice verde
