# Slice 003: UI de fechamento do NIR (resultado, ciência, encerrados)

## Objetivo

O NIR vê o **resultado** do caso (decisões + agendamento + resposta final em
destaque), confirma o recebimento (botão/POST) e dispõe da aba **encerrados**
em "meus casos" — tudo com escopo por criador e guards do papel `nir`.

## Contexto necessário

- Slices 001–002 entregues: `apps/cases/closure.py::acknowledge_case_receipt`
  (erros nomeados: estado errado, não-criador).
- `apps/intake/views.py` — `my_cases` (lista criador, sem abas hoje),
  `case_detail` (contexto com `communications` já renderizadas no
  `templates/intake/case_detail.html`; padrão `_require_user`); urls
  `intake:my_cases`, `intake:case_detail`.
- `apps/accounts/decorators.py::role_required("nir")` — guard por papel
  ativo (padrão das views do intake).
- Escopo por criador: padrão `_assert_owned_by`/404 do change 04
  (`apps/intake/services.py`) — replicar na view de ack (não vazar
  informação).
- `CaseProcedure.doctor_disposition/doctor_reason`, `Case.scheduled_*`
  (`SchedulingUnit`), labels do catálogo
  (`apps/cases/procedure_catalog.py` — helper de labels usado em
  `_procedure_labels` do intake).
- Design D3 (`openspec/changes/nir-result-closure/design.md`) — NIR vê dados
  reais (remetente); botão de ciência SÓ em `FINAL_REPLY_POSTED` + criador.

## Requisitos verificáveis

- **R1** `intake:case_detail` ganha seção de resultado quando o caso tem
  decisão médica: decisões por procedimento (label + disposição legível +
  motivo), dados de agendamento (unidade/data/local quando existirem) e a
  resposta final em destaque na thread; sem decisão médica, seção ausente.
- **R2** Botão "Confirmar recebimento" renderiza SOMENTE em
  `FINAL_REPLY_POSTED` com `created_by == request.user`; POST em nova rota
  `intake:case_ack` chama `acknowledge_case_receipt` com o papel ativo;
  sucesso → flash + redirect ao detalhe (caso `CLEANED`); erros do serviço →
  `messages.error` + redirect (nunca 500); não-criador → 404 (POST direto
  incluso).
- **R3** `intake:my_cases` com abas: **ativos** (default: tudo exceto
  `CLEANED`) e **encerrados** (apenas `CLEANED`) — mesmo formato de items,
  escopo criador, `?tab=` com fallback seguro.
- **R4** Guards: anônimo → login; papel ativo ≠ `nir` → 403 (padrão
  `role_required`); criador vê só os seus (filtro existente preservado).
- **R5** Testes de view: resultado exibido pós-decisão (conteúdo específico:
  motivo e unidade/data) e ausente pré-decisão; botão presente/ausente por
  estado+criador; ack via UI → `CLEANED` + flash; POST em estado errado →
  mensagem sem 500; não-criador → 404 (GET detalhe e POST ack); abas
  particionam corretamente (caso `CLEANED` só na encerrados; ativos não
  incluem `CLEANED`).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/intake/views.py`, `templates/intake/case_detail.html` | `test_detail_shows_outcome_after_decision`, `test_detail_no_outcome_before_decision` |
| R2 | `apps/intake/{views,urls}.py`, `templates/intake/case_detail.html` | `test_ack_button_visibility`, `test_ack_post_closes_case`, `test_ack_wrong_state_no_500`, `test_ack_non_creator_404` |
| R3 | `apps/intake/views.py`, `templates/intake/my_cases.html` | `test_my_cases_tabs_partition` |
| R4 | `apps/intake/views.py` | `test_ack_role_guard`, `test_tabs_creator_scope` |
| R5 | `apps/intake/tests/test_closure_views.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/intake/views.py                   # resultado + ack + abas
  - apps/intake/urls.py                    # +intake:case_ack
  - apps/intake/tests/test_closure_views.py
  - templates/intake/case_detail.html      # seção resultado + botão
  - templates/intake/my_cases.html         # abas

out_of_scope:
  - apps/cases (serviços dos slices 001–002 consumidos como estão)
  - reenvio corrigido (slice 004); dashboard/notificações (11)
  - estilos novos além do tema/Bootstrap existentes
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/intake/tests/test_closure_views.py`
- Falha esperada: `NoReverseMatch: 'intake:case_ack' not found` (rota/view
  inexistentes).

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/intake/tests/ apps/cases/tests/` —
  exit 0 (regressão: views existentes do intake + fechamento).
- `uv run ruff check apps/intake && uv run ruff format --check apps/intake`
- `uv run mypy .`

## Critérios de aceitação

- [ ] R1–R5 comprovados; ciência só do criador no estado certo (GET e POST)
- [ ] Resultado mostra motivo real e agendamento quando houver
- [ ] Abas particionam sem vazar casos de outros criadores
- [ ] Gate parcial do slice verde
