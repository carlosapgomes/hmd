# Slice 005: Ciência do NIR remove anexos (integração closure)

## Objetivo

`acknowledge_case_receipt` (change 09) passa a remover também os anexos na
limpeza: rows deletadas no atomic, arquivos físicos no mesmo `on_commit`
best-effort; nada de anexo sobrevive à ciência. A spec `case-closure` MODIFIED
(todos os cenários preservados + 1 novo) já está no change.

## Contexto necessário

- Slices 001–004 entregues (anexos existem com arquivos/textos/mapas).
- `apps/cases/closure.py::acknowledge_case_receipt` — conjunto D2 do change
  09 (docs + 7 campos zerados + preservados; `_delete_files_best_effort`
  pós-commit; `_CLEANED_EMPTY_VALUES`); testes existentes em
  `apps/cases/tests/test_closure_ack.py` (não enfraquecer).
- Delta spec: `openspec/changes/attachment-processing-ocr/specs/case-closure/
  spec.md` (MODIFIED com 6 cenários — 5 existentes + anexos).
- `apps/intake/views.py` bloco de anexos do detalhe (slice 001): caso limpo
  → lista vazia/ausente automaticamente (rows não existem) — teste de view
  opcional, o foco é o serviço.

## Requisitos verificáveis

- **R1** `acknowledge_case_receipt` coleta também os nomes de arquivos dos
  ANEXOS (antes do delete), deleta rows `case.attachments` no MESMO atomic
  da limpeza existente, e remove os arquivos físicos no mesmo
  `transaction.on_commit` best-effort (uma lista só de docs+anexos, ou
  duas chamadas — sem duplicar a lógica de log).
- **R2** Comportamento do change 09 preservado: conjunto zerado/preservado
  inalterado; 3 eventos na ordem; prior-case continua funcionando
  (regressões existentes do `test_closure_ack.py` continuam verdes).
- **R3** Testes: ciência em caso com anexos processados (rows+arquivos
  sumem — storage em memória + `django_db(transaction=True)` para o
  `on_commit`, padrão do slice 002 do 09); anexos de OUTRO caso intocados;
  caso sem anexos inalterado (regressão); trilha de eventos do caso ganha
  APENAS os 3 eventos de fechamento (os eventos de anexo permanecem como
  histórico — nada é deletado de `CaseEvent`).
- **R4** `openspec validate attachment-processing-ocr --strict` passa com o
  delta MODIFIED carregando os 6 cenários.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/cases/closure.py` | `test_ack_removes_attachments_rows_and_files` (transaction=True) |
| R2 | `apps/cases/closure.py` | suíte existente `test_closure_ack.py` verde |
| R3 | `apps/cases/tests/test_closure_ack.py` (adições) | `test_ack_other_case_attachments_untouched`, `test_ack_without_attachments_unchanged`, `test_ack_events_append_only` |
| R4 | `openspec/changes/.../specs/case-closure/spec.md` | `openspec validate attachment-processing-ocr --strict` |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/cases/closure.py                # limpeza inclui anexos
  - apps/cases/tests/test_closure_ack.py # +testes de anexo

out_of_scope:
  - delta spec (já existe no change desde o planejamento)
  - UI (NIR/doctor); reenvio corrigido; apps/attachments
  - mudar o conjunto D2 de campos do caso (só SOMA anexos à remoção)
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/cases/tests/test_closure_ack.py -k attachment`
- Falha esperada: `Failed: no tests ran` / asserção "rows de anexo não
  existem pós-ack" falha (rows sobrevivem).

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/cases/tests/ apps/attachments/tests/`
  — exit 0 (regressão closure + anexos).
- `uv run ruff check apps/cases && uv run ruff format --check apps/cases`
- `uv run mypy .`
- `openspec validate attachment-processing-ocr --strict`

## Critérios de aceitação

- [ ] R1–R4 comprovados; anexos (rows+arquivos) removidos na ciência junto
      com os documentos; trilha de eventos append-only
- [ ] Regressões do change 09 verdes; conjunto D2 intocado (soma apenas)
- [ ] validate --strict PASS com o delta MODIFIED completo (6 cenários)
- [ ] Gate parcial do slice verde
