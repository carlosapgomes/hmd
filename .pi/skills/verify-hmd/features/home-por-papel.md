# Home por papel

After login, each account reaches the work area of its (single) active role:
the NIR intake, the medical queue, the scheduler queue or the manager panel.
The home dispatcher (`/` → role route) is exercised by the real login
redirect, not by navigating directly to the destination.

## Sub-features

- `nir-home-intake`: `verify-nir` → `/intake/` with heading *Envio de
  relatório de regulação*.
- `doctor-home-queue`: `verify-doctor` → `/doctor/` with heading *Fila
  médica* (see [fila-medica.md](fila-medica.md)).
- `scheduler-home-queue`: `verify-scheduler` → `/scheduler/` with heading
  *Fila de agendamento*.
- `manager-home-panel`: `verify-manager` → `/dashboard/` with heading
  *Painel gerencial*.
- `admin-home-panel`: `verify-admin` (superuser, role `admin`) →
  `/dashboard/` with heading *Painel gerencial*.
- `role-badge-in-header`: the authenticated header shows the session
  (`display_name`, active-role badge, *Sair*) on every home page.

## How to get to it (user POV)

- Log in (the login redirects to `/`, which dispatches to the role area), or
  open `/` while authenticated.
- Each role's header nav links to its own area (*Enviar relatório*,
  *Fila médica*, *Fila de agendamento*, *Painel*).

## Driving it with Playwright

Same drive as [login.md](login.md): one isolated context per role, real-form
login, assert the landing URL contains the role path and the expected
heading is present, then snapshot (`<role>-home.png/.txt`). The drive never
navigates directly to the destination URL first — the landing must come from
the login redirect.

## Gotchas

- An alternate direct URL that works does NOT prove the login→home path; a
  landing failure stays FAIL even if `/doctor/` opens manually afterwards.
- The placeholder home (`accounts/home.html`, *Bem-vindo(a)*) only appears
  when the active role has no route in the dispatcher — none of the seeded
  single-role users should ever see it. Seeing it is a finding.
- Multi-role users and `/switch-role/` are outside the initial journey.
