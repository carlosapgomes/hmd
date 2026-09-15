# Slice 001 — Unidade de origem do cabeçalho (extração + campo)

```yaml
expected_files:
  - apps/intake/pdf_utils.py
  - apps/intake/tasks.py
  - apps/intake/services.py
  - apps/cases/models.py
  - apps/cases/migrations/
  - apps/intake/tests/test_header_metadata.py
  - apps/intake/tests/test_header_persistence.py
```

## Contexto necessário

- Layout real (corpus do piloto): `Unid. Origem:` rótulo SOZINHO (L31) com o
  nome da unidade na linha imediatamente seguinte (L32, ~7 palavras com
  sigla e hífen, ex. institucional) — MESMA forma multilinha de `Dias em
  tela`; há também a forma `Unidade de Origem:` no gate
  (`apps/intake/regulation_gate.py` — rótulos canônicos).
- `extract_header_metadata`/`HeaderMetadata` em `apps/intake/pdf_utils.py`
  (metadados vigentes); worker persiste no mesmo atomic
  (`apps/intake/tasks.py`); reenvio zera (`_RESUBMIT_CLEARED_FIELDS` em
  `apps/intake/services.py`); `_CLEANED_EMPTY_VALUES` (apps/cases/closure.py)
  NÃO deve mudar (campo sobrevive — administrativo).

## Goal

`Case.origin_unit` populado pela extração determinística do cabeçalho;
zerado no reenvio; preservado no CLEANED.

## Deliverables

### R1 — Extração pura

- `HeaderMetadata` ganha `origin_unit: str | None`; parser linha a linha
  de `Unid. Origem:`/`Unidade de Origem:` — valor na MESMA linha OU, rótulo
  sozinho, na linha imediatamente seguinte quando ela é um valor plausível
  (não-vazia; NÃO começa com rótulo de cabeçalho SESAB conhecido — reutilize
  a lista de rótulos do módulo). Primeira ocorrência; strip; truncada a
  128. Ausente → `None`.

### R2 — Campo + persistência

- `Case.origin_unit` (CharField(128), blank, default "") + migration.
- Worker pdf persiste junto dos metadados (mesmo atomic/gate).
- `_RESUBMIT_CLEARED_FIELDS` + zeramento `""` (mesma transação).

### R3 — Testes (RED→GREEN)

- Novos (RED): extração multilinha (rótulo sozinho + valor na seguinte) e
  mesma-linha; rótulo sozinho SEM valor plausível (próxima linha é outro
  rótulo) → `None`; worker persiste; reenvio zera (incluindo PDF
  corrompido); CLEANED preserva (paridade); eventos sem o valor;
  `ruff/format/mypy/makemigrations --check` + suíte.

## Gates para o reviewer (2 linhas)

1. Parser só captura valor plausível (linha seguinte não-vazia que não é
   rótulo de cabeçalho) — teste adversarial com rótulo seguido de rótulo.
2. `_CLEANED_EMPTY_VALUES` IDÊNTICO; `_RESUBMIT_CLEARED_FIELDS` contém
   `origin_unit` com `""`.

## Out of scope

- UI (cards do painel usam no slice 002); filas; specs de dashboard.
