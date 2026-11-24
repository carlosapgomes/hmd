# Slice 001 — Perfil sem troca de senha

## Identity

- Slice: S1
- Change: `bloqueio-troca-de-senha`
- Task: `tasks.md` → «S1 — Perfil sem troca de senha»

## Objective

Fazer do perfil uma página só de leitura, com a orientação de que a troca de
senha é feita pessoalmente na TI do hospital, e recusar qualquer escrita.

## Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md` — nota operacional de porta do banco de teste
- `openspec/changes/bloqueio-troca-de-senha/proposal.md`
- `openspec/changes/bloqueio-troca-de-senha/design.md` — D1 e D2
- `openspec/changes/bloqueio-troca-de-senha/specs/account-access/spec.md` —
  requisito «Perfil sem troca de senha»
- `apps/accounts/views.py` — `profile_view` (processa
  `HospitalPasswordChangeForm` no POST); `require_GET` já importado
- `apps/accounts/forms.py` — `HospitalPasswordChangeForm` (só o perfil usa)
- `templates/accounts/profile.html` — card «Alterar senha» na coluna direita
- `apps/accounts/tests/test_auth_flow.py` — `_create_user` (superusuário local
  autenticável), `TestLoginFlow::test_profile_password_change` (pin da troca
  atual, a inverter) e `test_login_logout_profile` (dados que permanecem)
- `templates/base.html` — o formulário de logout da navbar (linha ~104) é o
  único `<form>` que pode aparecer no HTML do perfil; não usa classes `col-*`
- `.pi/skills/verify-hmd/scripts/browser/drive-session-pages.mjs` — bloco
  «R3: perfil e manual» (helper `check(nome, condição, detalhe)`)
- `.pi/skills/verify-hmd/features/sessoes.md` — jornada `doctor-profile-manual`

## Requirements

- R1: `GET /profile/` autenticado exibe usuário, nome, e-mail, papéis com
  rótulos e conselho profissional, e o texto exato «Precisa trocar a senha?
  Procure pessoalmente a TI do hospital.»; o HTML **não** contém
  `type="password"`, `old_password`, `new_password` nem «Alterar senha».
- R2: `POST /profile/` autenticado com os campos do formulário antigo
  (`old_password`, `new_password1`, `new_password2`) responde **405**; a senha
  do usuário não muda (`check_password` da senha original segue verdadeiro) e a
  sessão continua válida (`GET /profile/` seguinte responde 200).
- R3: `GET /profile/` anônimo continua redirecionando ao login.
- R4: O card de dados ocupa a largura total: o HTML não contém `col-md-6` e a
  lista `<dl class="row profile-dl` vem depois de um wrapper `class="col-12"`.
- R5: `HospitalPasswordChangeForm` não existe mais em `apps/accounts` e
  `views.py` não importa `update_session_auth_hash`; `LoginForm` permanece
  intacto.
- R6: Harness `verify-hmd` acompanha: no bloco R3 do perfil,
  `drive-session-pages.mjs` passa a exigir `profile-cards >= 1` e ganha as
  checagens `profile-no-password-input` (nenhum `input[type=password]`),
  `profile-no-password-label` (HTML sem «Alterar senha») e
  `profile-password-guidance` (texto de orientação visível);
  `features/sessoes.md` descreve a jornada atualizada sem citar alteração de
  senha.
- R7: Os testes existentes que renderizam o perfil seguem verdes **sem
  alteração de asserções**: `test_auth_flow.py::test_login_logout_profile`,
  `test_page_titles.py::test_profile_page_title`, `test_header_nav.py`,
  `test_operational_nav_css.py`, `test_role_labels.py`,
  `test_notifications_session_pages.py::TestProfileAndManualPattern`.
- Evidência: R1–R4 por testes automatizados (novo arquivo
  `apps/accounts/tests/test_profile_no_password_change.py`; R2 também pela
  inversão de `test_profile_password_change`); R5 por inspeção executável
  (`grep` sem ocorrências + `ruff check` sem import não usado); R6 por
  inspeção dos dois arquivos e execução da jornada `sessoes` quando o
  ambiente permitir; R7 por execução dos testes listados.

## Expected blast radius

- Esperado: `apps/accounts/views.py`, `apps/accounts/forms.py`,
  `templates/accounts/profile.html`, `apps/accounts/tests/test_auth_flow.py`,
  teste novo em `apps/accounts/tests/`,
  `.pi/skills/verify-hmd/scripts/browser/drive-session-pages.mjs`,
  `.pi/skills/verify-hmd/features/sessoes.md`.
- Sensível/intocável: `apps/accounts/urls.py` (rota `profile/` e nome
  preservados), `templates/base.html` (avatar/link do perfil), backends,
  anti-lockout, `login.html`/`password_toggle.js`, manual
  (`templates/accounts/manual.html` — não menciona senha), CSS em
  `static/css/` (usar utilitários do Bootstrap; sem regra nova salvo
  justificativa).
- Comportamento preservado: `login_required` no perfil, dados exibidos hoje,
  rótulos de papel (`role_label`), `h1.page-title`, link «← Voltar».
- Expansão de blast radius exige justificativa no relatório.

## Failing-before plan

Failing-before evidence:
- targeted test: novo
  `uv run pytest apps/accounts/tests/test_profile_no_password_change.py -x`
  (escrito primeiro)
- expected failure: «Alterar senha» e `type="password"` presentes no GET;
  `POST` devolve 302 e altera a senha; `col-md-6` presente no HTML.
- O teste existente `test_profile_password_change` passa hoje; ele é
  REESCRITO para o contrato novo (405, senha inalterada) e a versão reescrita
  deve falhar antes da implementação e passar depois.

## Implementation constraints

- Decoradores na ordem `@login_required` (por fora) + `@require_GET` (por
  dentro); nenhuma leitura de `request.POST` na view.
- Texto de orientação exatamente como em D2, como parágrafo de texto
  secundário ao final do corpo do card de dados; sem link, botão ou formulário.
- Não tocar no admin (`apps/accounts/admin.py`, `config/admin.py`) — é o S2.
- Não adicionar nota ao manual nem CSS novo sem justificativa.

## Validation

Focused validation (worker):
- `uv run pytest apps/accounts/tests/test_profile_no_password_change.py apps/accounts/tests/test_auth_flow.py -x`
- `uv run pytest apps/accounts -x`
- `grep -rn "HospitalPasswordChangeForm\|update_session_auth_hash" apps templates` sem saída

Project-required gates: `AGENTS.md` §2 (ruff, format, mypy, pytest).

Runtime/verification evidence: jornada `sessoes` do `verify-hmd`
(`control-hmd doctor --run <dir>` e
`node .pi/skills/verify-hmd/scripts/browser/drive-session-pages.mjs --run <dir>`)
com `profile-cards`, `profile-no-password-input`, `profile-no-password-label`
e `profile-password-guidance` sem falhas, e o snapshot do perfil sem campo de
senha. Se a estação do worker não tiver os pré-requisitos (banco do verify,
Playwright), registrar no relatório como limitação para a controladora executar.

## Acceptance criteria

- [ ] R1 GET exibe dados + orientação; sem campo/rótulo de senha
- [ ] R2 POST → 405, senha inalterada, sessão válida
- [ ] R3 anônimo → redirect ao login
- [ ] R4 card em largura total, sem `col-md-6`
- [ ] R5 formulário de troca removido; sem import morto
- [ ] R6 harness atualizado (script + feature) e jornada `sessoes` sem falhas
- [ ] R7 testes existentes do perfil verdes sem mudança de asserções
- [ ] Failing-before evidence registrada
- [ ] Gates do projeto verdes no escopo tocado

## Escalation conditions

Pare e retorne `BLOCKED_NEEDS_DECISION` se: contradição com spec/design;
necessidade de mexer no admin (S2) para fechar este slice; pedido de manter
qualquer forma de troca de senha; expansão material de escopo.

## Evidence report

`/tmp/hmd-slice-bloqueio-senha-001-report.md` — status
(`READY_FOR_REVIEW`/`BLOCKED_NEEDS_DECISION`), requirements→evidence,
arquivos alterados, failing-before, passing-after, comandos+exit, evidência
de runtime, riscos.

## Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy in `AGENTS.md` and preserve existing
conventions. Prefer the smallest correct change; avoid speculative abstraction
and unrelated refactoring.
Produce failing-before evidence, implement the smallest correct change, run
focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
