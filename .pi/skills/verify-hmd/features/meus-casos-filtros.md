# Meus casos — filtros server-side e auto-refresh (ui-parity-queues slice 003)

Journey `meus-casos-filtros`: the NIR "meus casos" list
(`/intake/cases/`, cards `case-card`) with **server-side** filters
(`q` by occurrence number with 3+ chars, `status`, `procedure_type`
from the 13-type catalog — textual search is server, never client-side),
pills `Ativos/Encerrados` with counters as the tab navigation (restyled,
not removed — the `_nav` is the section navigation, no duplication)
and htmx auto-refresh every 20 s from `intake:my_cases_partial` (same
fragment, same guards, same creator scope) preserving tab and filters.
The JS (`nir_queue_filter.js`) only reapplies the visible-card counter
after each swap — it never filters cards in the browser.

## Sub-features

- `my-cases-cards-parity`: cards `case-card + summary-box + waiting-time +
  status-badge`, patient name + age (`64 a`), occurrence `VERIFY-001`,
  declared procedures, retention/status badges; pills `Ativos`/`Encerrados`
  with count badges link to `?tab=active|closed` preserving filters.
- `my-cases-server-filters`: `q=VERIFY-001` narrows to the matching card
  (server round-trip); `q` with 2 chars is ignored (never zeroes the
  list); `status` and `procedure_type` narrow by state/declared type;
  combined filters compose by AND.
- `my-cases-creator-scope`: a foreign case matching every filter
  (`VERIFY-X01`, created by another NIR) never appears — in the full
  page or in the partial.
- `my-cases-auto-refresh-partial`: the container `#my-cases-content`
  polls `cases/partials/list/` every 20 s (`hx-trigger="every 20s"`),
  with the active filters in the poll URL; a compatible new case
  (`VERIFY-M10`, `FAILED` + `art_perif`) appears without reload after
  the `htmx:afterSwap` with the tab and the filters (`FAILED` +
  `art_perif`) still applied; an off-filter new case (`VERIFY-M11`,
  status `NEW`) never appears after the swap.

## How to get to it (user POV)

- Log in as a NIR (real form) — the dispatcher lands on
  `/intake/cases/?tab=active`.
- Read the cards under *Ativos*; use *Buscar por número de ocorrência*,
  *Filtrar por status* and *Filtrar por tipo de procedimento*; switch
  between *Ativos* and *Encerrados* (filters are preserved).
- Leave the page open: the list refreshes by itself every ~20 s.

## Driving it with Playwright

Standalone driver (same ownership/login standards as the other drives —
real form login, pinned Chromium, owned loopback instance only):

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <run-dir>
node .pi/skills/verify-hmd/scripts/browser/drive-meus-casos-filtros.mjs --run <run-dir>
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <run-dir>
```

The driver logs in as `verify-nir` through the real form, creates the
NIR fixtures (see Gotchas) through the verify settings before login,
asserts the parity markers + htmx wiring + pills, drives the server
filters (q, short q, status, procedure type, AND), then runs the
refresh checks with the filters (`status=FAILED` +
`procedure_type=art_perif`) active: creates one compatible synthetic
case (`VERIFY-M10`) through the verify settings as setup, waits for
the 20 s poll response and the `htmx:afterSwap` of the container and
asserts the new card appears without reload with tab/filters
preserved, then asserts an off-filter new case (`VERIFY-M11`) never
appears after the swap — and snapshots each step
(`nir-meus-casos-*.png/.txt`) under `<run-dir>/artifacts/`.
Exit codes: `0` PASS, `1` observed application failures, `2`
infrastructure/driver blocker (never switch engines).

## Gotchas

- The seed already creates `VERIFY-001`/`VERIFY-002` (created by
  `verify-nir`, `AWAITING_DOCTOR`) — the driver only adds
  `VERIFY-M01` (own, `FAILED`), `VERIFY-M02` (own, `CLEANED`,
  closed tab), `VERIFY-M03` (`cat_cardiaco` only) and the foreign
  `VERIFY-X01` (another NIR) before login.
- The poll is 20 s: the refresh steps wait for the partial response
  and for the `htmx:afterSwap` of `#my-cases-content` before asserting
  the DOM (the HTTP response alone does not prove the swap), they
  never force a reload. A missing poll or swap means the htmx wiring
  regressed — report FAIL, never reload to make it pass.
- Cleanup drops the whole database, so no state leaks between runs.
