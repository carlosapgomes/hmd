# Slice 001 — Helper de referência humana no model `Case`

```yaml
expected_files:
  - apps/cases/models.py
  - apps/cases/tests/test_case_reference.py
allowed_incidental_files: []
out_of_scope:
  - qualquer template/view/presenter (slices 002-005)
  - `Case.__str__`, migrations, FSM
```

## Objetivo

`Case` expõe as três propriedades puras que substituem o uid nas superfícies
visíveis dos slices seguintes — fonte única da regra de fallback (D1 do
design).

## Contexto necessário

- `apps/cases/models.py` — `case_id` (UUIDField PK, `:106`) e
  `agency_record_number` (`CharField(max_length=20, blank=True)`, `:124`,
  não único). Basta acrescentar as properties junto dos demais helpers do
  model; nenhuma migration (campos existentes).
- Formato do fallback (decisão do dono, padrão do painel): `uid a1b2c3d4`
  (8 primeiros chars). Desambiguação: `2024-1234 · a1b2c3d4`.
- Testes do app ficam em `apps/cases/tests/` (ver `test_fsm.py` para
  convenção de fixtures/factories).

## Requisitos verificáveis

- **R1** — `Case.short_id` retorna `str(case_id)[:8]`.
- **R2** — `Case.display_reference` retorna `agency_record_number.strip()`
  quando preenchido; senão `f"uid {short_id}"` (string vazia/whitespace
  conta como ausente).
- **R3** — `Case.disambiguated_reference` retorna
  `f"{nº} · {short_id}"` quando há nº; senão `f"uid {short_id}"`
  (sufixo seria redundante).
- **R4** — Propriedades puras: não consultam banco, não mutam estado,
  retornam `str` (mypy strict cobre `tests/`).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/cases/models.py` | `pytest apps/cases/tests/test_case_reference.py -k short_id` |
| R2 | `apps/cases/models.py` | `pytest apps/cases/tests/test_case_reference.py -k display_reference` |
| R3 | `apps/cases/models.py` | `pytest apps/cases/tests/test_case_reference.py -k disambiguated_reference` |
| R4 | `apps/cases/models.py` | `uv run mypy apps/cases/models.py` + revisão (sem query/mutação) |

## Plano de testes

### RED

- Comando: `uv run pytest apps/cases/tests/test_case_reference.py -q`
- Falha esperada: `AttributeError`/coleta falha — as properties ainda não
  existem no model (teste novo referencia `case.display_reference` etc.).

### GREEN / verificação local

- `uv run pytest apps/cases/tests/test_case_reference.py -q` — exit 0
- `uv run mypy apps/cases/models.py` — exit 0
- `uv run ruff check apps/cases && uv run ruff format --check apps/cases` — exit 0

## Critérios de aceitação

- [ ] R1-R4 verdes; testes cobrem nº presente, nº vazio e nº com whitespace
- [ ] Sem query/mutação nas properties; sem mudança fora de `models.py` + teste novo
- [ ] Nenhuma surface de UI tocada (é papel dos slices 002-005)

## Handoff

Slice autocontido: implementar exatamente as properties do design (D1). Se
precisar tocar qualquer outro arquivo além dos listados, escalar ao parent.
