# Tasks — case-detail-human-reference

## Preflight

- [ ] Confirmar árvore limpa na branch de trabalho, registrar `BASE_REF` e rodar a suíte completa uma vez (`uv run pytest -q`) para baseline

## Implementação

- [x] Slice 001 — Helper de referência humana no model `Case` (`short_id`, `display_reference`, `disambiguated_reference`) — `slices/slice-001-case-reference-helper.md`
- [x] Slice 002 — Detalhe do NIR: card «Identificação do paciente» + referência humana no title/breadcrumb/link de correção — `slices/slice-002-nir-detail-patient-card.md`
- [x] Slice 003 — Textos do fluxo NIR: 6 flash messages + alerta de lote + página «Reenviar corrigido» com referência (desambiguada no reenvio) — `slices/slice-003-nir-flow-texts.md`
- [x] Slice 004 — Meus casos: sufixo curto de desambiguação nos cards com nº duplicado — `slices/slice-004-my-cases-duplicate-suffix.md`
- [x] Slice 005 — Doctor + scheduler: referência no title/breadcrumb (detalhe e decisão) e badge do prior-case — `slices/slice-005-doctor-scheduler-references.md`

## Gate final

- [x] `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest` — tudo verde, sem regressão vs. baseline
- [x] `openspec validate case-detail-human-reference --strict`

## Pós-gate (parent)

- [ ] Atualizar `PROJECT_CONTEXT.md` com o estado do change
