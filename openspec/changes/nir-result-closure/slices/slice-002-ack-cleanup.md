# Slice 002: Ciência do NIR + limpeza transacional

## Objetivo

`acknowledge_case_receipt`: o criador confirma o recebimento da resposta
final e o caso vai a `CLEANED` no mesmo atomic — com minimização de dados
(documentos+arquivos, textos, mapa de pseudônimos e artefatos LLM removidos;
identificação/decisões/trilha/comunicações/agendamento preservados) e sem
quebrar o prior-case.

## Contexto necessário

- Slice 001 entregue (`apps/cases/closure.py` existe).
- `apps/cases/models.py` — ops públicas `nir_acknowledge`, `start_cleaning`,
  `complete_cleaning` (todas `*, user, role`); campos a zerar:
  `extracted_text`, `anonymized_text`, `pseudonym_map` (JSONField dict →
  `{}`), `structured_data`, `summary_text`, `suggested_action`,
  `policy_result`; `CaseDocument` (related `documents`, `file` FileField).
- `apps/pipeline/prior_case.py::lookup_prior_case_context(case,
  procedure_type)` — lê APENAS `CaseProcedure` + identificação do caso
  (verificar nos fontes: não lê artefatos) — base da regressão R5.
- Design D1/D2 (`openspec/changes/nir-result-closure/design.md`) — ordem de
  deleção (rows no atomic; arquivos físicos best-effort em
  `transaction.on_commit`); preservados listados.
- Padrão do storage em teste: os testes do intake usam `InMemoryStorage`
  via fixture autouse (`apps/intake/tests/conftest.py` — replicar esse
  fixture para os testes deste slice, ex.: em `apps/cases/tests/conftest.py`
  ou override no módulo; sem isso os arquivos iriam para o `MEDIA_ROOT`
  real).
- `transaction.on_commit` **não dispara** em teste `django_db` comum
  (rollback descarta callbacks): o teste de deleção física usa
  `@pytest.mark.django_db(transaction=True)` (precedente:
  `apps/pipeline/tests/test_signals.py`) — atenção ao quirk de harness
  conhecido (flush apaga seeds; fixtures `get_or_create` como nos demais).

## Requisitos verificáveis

- **R1** `acknowledge_case_receipt(case, *, user, role)` em
  `apps/cases/closure.py`: valida estado == `FINAL_REPLY_POSTED` E
  `case.created_by == user` (erros nomeados distintos, sem efeito); no MESMO
  atomic (`select_for_update`): coleta nomes de arquivos das rows →
  encadeia `nir_acknowledge` → `start_cleaning` → **limpeza** →
  `complete_cleaning` (3 eventos `CASE_STATUS_*`: AWAITING_NIR_ACK, CLEANING,
  CLEANED).
- **R2** Limpeza remove/preserva EXATAMENTE o D2: deleta rows
  `case.documents`; zera `extracted_text`/`anonymized_text`/
  `pseudonym_map`/`structured_data`/`summary_text`/`suggested_action`/
  `policy_result`; PRESERVA `patient_name`/`patient_birth_date`/
  `agency_record_number`, `anonymization_report`, rows `CaseProcedure`,
  `CaseEvent`, comunicações e `scheduled_*` (assert de preservação
  explícito nos testes).
- **R3** Arquivos físicos deletados best-effort APÓS commit
  (`transaction.on_commit`), com log em falha; arquivos de OUTROS casos
  intocados. Teste com `django_db(transaction=True)` + storage em memória
  (callbacks de commit disparam de verdade).
- **R4** Estado errado / não-criador → erros nomeados sem nenhuma escrita.
- **R5** Regressão prior-case: caso `CLEANED` com decisão médica
  (`doctor_decided_at`) **dentro da janela** — a janela do prior-case mede
  `doctor_decided_at` da row prévia contra `created_at` do caso novo (7d nº
  de registro / 15d nome+nascimento; settings do projeto), NÃO a idade da
  limpeza — segue retornado por `lookup_prior_case_context` de um novo caso
  do mesmo paciente/tipo (registro nº igual; ancorar o setup na data da
  decisão, não na data do fechamento).
- **R6** Testes: happy path (estado/3 eventos na ordem/rows deletas/campos
  zerados/preservados intactos/arquivo físico sumiu do storage); estado
  errado; não-criador; prior-case pós-limpeza; **documento inacessível via
  rota `intake:serve_document` → 404 (teste OBRIGATÓRIO — cenário de
  spec)**.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1/R4 | `apps/cases/closure.py` | `test_ack_chains_to_cleaned`, `test_ack_wrong_state_rejected`, `test_ack_non_creator_rejected` |
| R2 | `apps/cases/closure.py` | `test_cleanup_removes_clinical_keeps_essential` |
| R3 | `apps/cases/closure.py` | `test_ack_deletes_physical_files_after_commit` (django_db transaction=True), `test_ack_other_case_files_untouched` |
| R5 | `apps/cases/tests/test_closure_ack.py` | `test_cleaned_case_still_prior_case` (setup ancorado em `doctor_decided_at`) |
| R6 | `apps/cases/tests/test_closure_ack.py` | suíte do slice + `test_serve_document_404_after_cleanup` (rota) |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/closure.py                # +acknowledge_case_receipt
  - apps/cases/tests/test_closure_ack.py

out_of_scope:
  - UI/rota de ack (slice 003); reenvio corrigido (slice 004)
  - migration (nenhuma — só zera campos existentes)
  - apps/pipeline/prior_case.py (consumido como está)
  - workers/clusters (limpeza é síncrona — D6)
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/cases/tests/test_closure_ack.py`
- Falha esperada: `ImportError: cannot import name 'acknowledge_case_receipt'`.

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/cases/tests/ apps/pipeline/tests/
  apps/intake/tests/` — exit 0 (regressão: prior-case + intake docs).
- `uv run ruff check apps/cases && uv run ruff format --check apps/cases`
- `uv run mypy .`

## Critérios de aceitação

- [ ] R1–R6 comprovados; limpeza remove EXATAMENTE o conjunto do D2 (nada a
      mais, nada a menos — asserts de preservação)
- [ ] Fecho encadeado num único atomic (3 eventos; AWAITING_NIR_ACK/CLEANING
      transitórios)
- [ ] Prior-case funciona com caso limpo (R5)
- [ ] Gate parcial do slice verde
