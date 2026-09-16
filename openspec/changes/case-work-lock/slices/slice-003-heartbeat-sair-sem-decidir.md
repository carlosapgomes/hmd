# Slice 003 — Heartbeat, «Sair sem decidir» e submit guard

## Objetivo

A reserva viva enquanto o médico trabalha: renovação automática por heartbeat
JavaScript com detecção de atividade, aviso ao perder a reserva, liberação
best-effort ao navegar para fora (pagehide/clique em link) com submit guard,
e «Sair sem decidir» como ação server-side que libera a reserva na hora sem
depender de JavaScript.

## Contexto necessário (leia antes)

- `openspec/changes/case-work-lock/design.md` — seção D3.
- **Fonte a adaptar (copiar e ajustar)**: `/projects/dev/ats-web/static/js/
  work_lock.js` (229 linhas, vanilla, sem dependências) — manter a lógica
  (atividade, heartbeat, submit guard, release triggers) e ajustar
  cabeçalho/comentários para o HMD.
- `apps/doctor/urls.py`, `apps/doctor/views.py` — rotas/views do doctor
  (`case_decide`, `queue`); guards de acesso `_require_user` +
  `can_access_case` com `active_role`.
- `templates/doctor/decide.html` (após slice 002: formulário com
  `lock_token`), `templates/base.html` (inserir `<meta name="csrf-token">`).
- `apps/cases/locks.py` — `renew_case_lock`, `release_case_lock`,
  `assert_case_lock` (assinaturas por instância+token; `CaseLockConflictError`).
- `apps/doctor/tests/test_decision.py` — padrões de teste da view.

## Requisitos verificáveis

- **R1** `POST doctor:lock_renew` (`case/<uuid>/lock/renew/`): exige
  `lock_token`; posse válida → `renew_case_lock` e JSON
  `{"success": true, "locked_until": <iso>}`; token ausente/inválido/expirado
  → `{"success": false, "error": …}` (HTTP 200, sem exceção).
- **R2** `POST doctor:lock_release`: posse válida → `release_case_lock` e
  `{"success": true}`; sem posse → `{"success": false}` (idempotente).
- **R3** `POST doctor:case_leave` (`case/<uuid>/leave/`): com token válido →
  release + redirect à fila com flash informativo; sem token/inválido →
  apenas redirect à fila (sem erro, idempotente).
- **R4** `decide.html`: botão «Sair sem decidir» vira mini-form POST para
  `case_leave` com `lock_token` (server-side garantido) — mantém aparência de
  botão outline ao lado do submit.
- **R5** `decide.html` inclui `#work-lock-config` (data-renew-url,
  data-release-url, data-lock-token, data-heartbeat-ms="60000",
  data-grace-ms="240000") e `#work-lock-warning` oculto.
- **R6** `static/js/work_lock.js` presente, carregado por `decide.html`,
  com: tracking de atividade, heartbeat condicionado à grace, submit guard
  para forms com `lock_token`, release best-effort (keepalive) em pagehide e
  clique em link de navegação externa (âncora/`_blank` ignoradas).
- **R7** `base.html` exporta `<meta name="csrf-token">` (leitura pelo JS).

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/doctor/urls.py
  - apps/doctor/views.py            # lock_renew, lock_release, case_leave
  - static/js/work_lock.js          # novo (adaptado do ATS)
  - templates/doctor/decide.html    # config/warning + botão sair sem decidir
  - templates/base.html             # meta csrf-token
  - apps/doctor/tests/test_decision.py  # R1–R4 (novos)

allowed_incidental_files: []

out_of_scope:
  - qualquer mudança em locks.py (serviços já existem)
  - fila/cards (slice 002)
  - scheduler (slice 004)
  - testes de JavaScript (não há runner JS no projeto — R5–R7 verificados por
    render + rg, ver matriz)
```

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) | Teste/check |
| --- | --- | --- |
| R1 | `urls.py`, `views.py` | `test_decision.py::test_lock_renew_endpoint` (renova `locked_until`; JSON sucesso; token errado → success false) |
| R2 | `urls.py`, `views.py` | `test_decision.py::test_lock_release_endpoint` (limpa lock; JSON; idempotente) |
| R3 | `urls.py`, `views.py` | `test_decision.py::test_case_leave_releases_and_redirects` (+ variante sem token só redirect) |
| R4 | `decide.html` | `test_decision.py::test_leave_button_is_form_post` (`action` do case_leave + `lock_token` no corpo, `0 < pos`) |
| R5 | `decide.html` | `test_decision.py::test_work_lock_config_rendered` (atributos com `0 < pos`) |
| R6 | `work_lock.js`, `decide.html` | render carrega `work_lock.js`; `rg -n "data-heartbeat-ms\|pagehide\|ATS_WORK_LOCK_SUBMITTING\|keepalive" static/js/work_lock.js` mostra os marcos |
| R7 | `base.html` | `rg -n 'meta name="csrf-token"' templates/base.html` |

## RED

- Comando: `TEST_DB_PORT=5433 uv run pytest -q apps/doctor/tests/test_decision.py -k "renew_endpoint or release_endpoint or case_leave or leave_button or work_lock_config"`
- Falha esperada: 404 nas rotas novas / ausência dos blocos no render —
  motivo de rota/render, não de setup.

## GREEN / verificação local

- `TEST_DB_PORT=5433 uv run pytest -q apps/doctor/tests/test_decision.py` — exit 0
- `rg -n "data-heartbeat-ms" templates/doctor/decide.html` e
  `rg -n "keepalive" static/js/work_lock.js` — presentes
- `uv run ruff check apps && uv run mypy apps` — exit 0

## Critérios de aceitação

- [ ] R1–R7 demonstrados
- [ ] Endpoints nunca levantam 500 (tudo responde JSON/redirect)
- [ ] JS adaptado sem dependências externas e sem `console.log` de produção
      (mantém o gate `data-debug`)
- [ ] Sem arquivo fora do blast radius; sem commits/tasks.md
