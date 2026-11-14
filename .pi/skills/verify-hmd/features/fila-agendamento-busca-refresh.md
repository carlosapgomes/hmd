# Fila do agendador — busca client-side e auto-refresh (ui-parity-queues slice 002)

Journey `fila-agendamento-busca-refresh`: the scheduler queue
(`/scheduler/`, cards `case-card`) with client-side search by
name/occurrence (no reload, no request), lock states reflected on the
cards (Abrir / Continuar / Reservado + holder) and htmx auto-refresh
every 20 s from `scheduler:queue_partial` (same fragment, same guards)
re-applying the active search. The queue is complete — no unit
differentiation (both scheduled units' cases are listed together on the
processed tab; `unit_label` is display only).

## Sub-features

- `queue-cards-parity`: cards `case-card + summary-box + waiting-time +
  status-badge`, patient name + age (`64 a`), occurrence `VERIFY-S01`,
  `3 d em tela`, declared procedures with subtype badges, the alert
  `pulse-dot` (computed style: 8×8, danger token, `pulse-dot` animation)
  and page 1 full (20 cards); no legacy `nav-tabs` block (navigation is
  the `_nav` pills `Pendentes/Processados` with count badges).
- `queue-lock-states`: free card offers `Abrir caso` (link to the
  detail), the scheduler's own lock offers `Continuar`, and another
  scheduler's lock shows the disabled `Reservado` button with the
  holder name and no opening action.
- `queue-search-without-reload`: typing the patient name or the
  occurrence number in the search input hides non-matching cards in
  place (same URL, no navigation); the ARIA status
  (`data-scheduler-queue-filter-status`, `aria-live="polite"`) reports
  the visible count and the no-results alert appears for a non-matching
  term; clearing restores all cards.
- `queue-auto-refresh-partial`: the container `#scheduler-queue-content`
  polls `partials/queue/` every 20 s (`hx-trigger="every 20s"`); the
  driver enters the term `VERIFY-S03` (matching only the new case)
  before the poll, and after the swap asserts the term is still in the
  input, the matching new case (`VERIFY-S03`) is visible and a
  non-matching card (`Paciente Sintético Agendamento Um`) stays hidden
  (exactly one visible card) — i.e. the `htmx:afterSwap` reapplication
  under an active filter. The out-of-page case (`VERIFY-S99`, null
  `days_on_screen`, last position) never reaches the swapped fragment.

## How to get to it (user POV)

- Log in as a scheduler (real form) — the dispatcher lands on
  `/scheduler/`.
- Read the cards under *Pendentes*; type in *Buscar por nome ou
  ocorrência*; watch the cards filter in place.
- Leave the page open: the list refreshes by itself every ~20 s.

## Driving it with Playwright

Standalone driver (same ownership/login standards as the other drives —
real form login, pinned Chromium, owned loopback instance only):

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <run-dir>
node .pi/skills/verify-hmd/scripts/browser/drive-fila-agendamento-busca-refresh.mjs --run <run-dir>
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <run-dir>
```

The driver logs in as `verify-scheduler` through the real form, creates
the scheduler fixtures (see Gotchas) through the verify settings before
login, asserts the parity markers (incl. the computed `pulse-dot`
style) + htmx wiring + lock states, drives the search (name, then
occurrence, then a non-matching term, then clear), creates one
compatible synthetic case (`VERIFY-S03`) through the verify settings as
setup, enters `VERIFY-S03` in the search before the 20 s poll, waits
for the poll and asserts the term is preserved, the matching new card
is visible, a non-matching card stays hidden and the out-of-page case
is absent — all without reload — and snapshots each step
(`scheduler-queue-busca-*.png/.txt`) under `<run-dir>/artifacts/`.
Exit codes: `0` PASS, `1` observed application failures, `2`
infrastructure/driver blocker (never switch engines).

## Gotchas

- The seed only creates `AWAITING_DOCTOR` cases, so the driver creates
  the scheduler fixtures itself (processing + doctor approval to
  `SCHEDULER_REQUESTED`): `VERIFY-S01`/`VERIFY-S02`, 17 filler cases
  (page 1 full), `VERIFY-S10` (own lock → `Continuar`), `VERIFY-S11`
  (another scheduler's lock → `Reservado` + holder, no action) and
  `VERIFY-S99` (null `days_on_screen`, out of page 1) before login.
- The poll is 20 s: the refresh step waits for the partial response,
  it does not force a reload. A missing poll means the htmx wiring
  regressed — report FAIL, never reload to make it pass.
- Opening a card claims the case lock (`scheduler_confirm`) — the
  journey never clicks a card (lock fixtures are claimed directly in
  the setup), so no lock is claimed by navigation. Cleanup drops the
  whole database, so no state leaks between runs.
