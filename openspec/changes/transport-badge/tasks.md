# Tasks

Cada task referencia o contrato do seu slice em `slices/` — o slice é o
contrato executável (requisitos, blast radius, failing-before, critérios de
aceitação, escalada); este arquivo apenas indexa a sequência.

- [x] S1 — Extração e persistência do tipo de transporte
  Contract: slices/slice-001-extracao-persistencia.md
  (função pura + campo `Case.transport_type` + migration 0015 + write do
  worker + ciclo de vida: reenvio zera, `CLEANED` preserva; verificação do
  corpus real 9/9 sem commitar)

- [ ] S2 — Badge de transporte nas três filas
  Contract: slices/slice-002-badge-filas.md
  (mapeamento único de classe em `apps/cases/models.py`; badge nos cards de
  meus casos, fila médica e fila do agendador; paridade de rótulo/classe)

- [ ] S3 — Transporte no detalhe do caso médico
  Contract: slices/slice-003-detalhe-medico.md
  (linha «Transporte» no card de identificação; ausente → «—»)

- [ ] S4 — Gate final da change (após S1–S3 aceitos): rodar o gate completo
  (`uv run ruff check . && uv run ruff format --check . && uv run mypy . &&
  uv run pytest`) e `openspec validate transport-badge --specs --strict`;
  conferir no diff que nenhum evento/trilha carrega o valor do transporte e
  que pipeline LLM/anonização não foi tocado.
