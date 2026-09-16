# Tasks: case-work-lock

## Preflight (parent, uma vez)

- [x] Working tree limpa pós-release v0.1.12; `BASE_REF` registrado (HEAD `fe13662`)
- [x] Suíte completa verde como baseline (1466)

## Slices (ordem executável)

- [x] 1.1 Slice 001 — página de trabalho única do médico (fila → `case_decide`
      com leitura completa + form no fim, âncora, botões, partial
      `_case_cards.html`)
- [x] 1.2 Slice 002 — reserva do médico (lease por contexto, claim no GET,
      fila 🔒/Continuar/Avaliar, POST exige token + release determinístico,
      expiração lazy)
- [x] 1.3 Slice 003 — heartbeat (`work_lock.js` + endpoints renew/release,
      submit guard, meta csrf) e «Sair sem decidir» server-side (`case_leave`)
- [x] 1.4 Slice 004 — reserva do agendador (claim no detalhe agendável, POSTs
      com token, fila, JS e sair sem decidir espelhados)

## Gate final (parent, uma vez após todos os slices)

- [x] `TEST_DB_PORT=5433 uv run pytest -q` — suíte completa verde (1511)
- [x] `uv run ruff check . && uv run ruff format --check .`
- [x] `uv run mypy .`
- [x] `uv run python manage.py check --settings=config.settings.dev`
- [x] `openspec validate case-work-lock --strict` (e `--all` após archive)
- [x] E2E dev: dois médicos e dois agendadores (reserva, barreira com nome,
      renovação, liberação ao decidir/sair, confirmação → FINAL_REPLY_POSTED)
