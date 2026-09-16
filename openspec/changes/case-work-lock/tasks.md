# Tasks: case-work-lock

## Preflight (parent, uma vez)

- [ ] Working tree limpa pós-release v0.1.12; `BASE_REF` registrado (HEAD `fe13662`)
- [ ] Suíte completa verde como baseline (1466)

## Slices (ordem executável)

- [ ] 1.1 Slice 001 — página de trabalho única do médico (fila → `case_decide`
      com leitura completa + form no fim, âncora, botões, partial
      `_case_cards.html`)
- [ ] 1.2 Slice 002 — reserva do médico (lease por contexto, claim no GET,
      fila 🔒/Continuar/Avaliar, POST exige token + release determinístico,
      expiração lazy)
- [ ] 1.3 Slice 003 — heartbeat (`work_lock.js` + endpoints renew/release,
      submit guard, meta csrf) e «Sair sem decidir» server-side (`case_leave`)
- [ ] 1.4 Slice 004 — reserva do agendador (claim no detalhe agendável, POSTs
      com token, fila, JS e sair sem decidir espelhados)

## Gate final (parent, uma vez após todos os slices)

- [ ] `TEST_DB_PORT=5433 uv run pytest -q` — suíte completa verde
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mypy .`
- [ ] `uv run python manage.py check --settings=config.settings.dev`
- [ ] `openspec validate case-work-lock --strict` (e `--all` após archive)
- [ ] E2E dev: dois médicos (reserva/barreira/liberação), inatividade
      (expiração), decisão e sair sem decidir; agendador das duas unidades
