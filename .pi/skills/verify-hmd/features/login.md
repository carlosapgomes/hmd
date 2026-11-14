# Login

A user opens SAR – HMD, authenticates with the local login form, reaches the
work area of their active role and keeps the session in the browser context
created by the real form POST. Verification uses the synthetic seed users —
never a hospital account, never an injected cookie.

## Sub-features

- `login-form-renders`: `GET /login/` shows the *Usuário*/*Senha* form with
  the `Entrar` button and the transitory-access notice.
- `login-doctor-lands-on-queue`: `verify-doctor` submitted through the form
  lands on `/doctor/` (single role → active role auto-set by the middleware).
- `login-nir-lands-on-intake`: `verify-nir` lands on `/intake/`.
- `login-scheduler-lands-on-queue`: `verify-scheduler` lands on `/scheduler/`.
- `login-manager-admin-land-on-panel`: `verify-manager` and `verify-admin`
  land on `/dashboard/`.
- `wrong-password-no-session`: an invalid password does not create a session
  and returns to the form with the generic error message.

## How to get to it (user POV)

- Open `/login/` directly (or any protected URL while anonymous — Django
  redirects to the login form).
- Type the username and password, click **Entrar**.

## Driving it with Playwright

Preconditions: an owned run passes `doctor`; then
`.pi/skills/verify-hmd/scripts/control-hmd drive --run <run-dir>` (or
`node .pi/skills/verify-hmd/scripts/browser/drive.mjs --run <run-dir>`).
The drive fills `getByLabel('Usuário')` and `getByLabel('Senha')`, clicks
`Entrar`, waits for the URL to leave `/login/`, and snapshots the form
before the action (`<role>-login-before.png/.txt`) plus the landing page.
Selectors come from the real accessibility snapshot — never copy a selector
from an older run.

## Gotchas

- The synthetic backend is verification-only: it refuses to load under
  `config.settings.dev`/`test`/`prod` (negative tests in `SKILL.md`). A login
  failure under verify settings is a genuine finding, not a reason to bypass
  the form or inject a cookie.
- Single-role users get their active role auto-set; multi-role selection
  (`/switch-role/`) is not covered by the initial journey.
- Session proof is the authorized landing page in the same context — the
  HttpOnly session cookie is never extracted or logged.
