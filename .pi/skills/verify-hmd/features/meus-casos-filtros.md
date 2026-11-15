# Meus casos — filtros server-side e auto-refresh (OLI-004)

Journey `meus-casos-filtros`: the NIR "meus casos" list
(`/intake/cases/`, cards `case-card`) with **server-side** filters
(`q` by occurrence number with 3+ chars, `status`, `procedure_type`
from the 13-type catalog — textual search is server, never client-side),
single header navigation `Meus Casos`/`Casos Encerrados` with counters
preserving `filter_qs` (no internal pills), neutral list header with a
single visual count, compact 4/3/3/2 cards, and htmx auto-refresh every
20 s from `intake:my_cases_partial` (same fragment, same guards, same
creator scope) preserving tab and filters. The JS
(`nir_queue_filter.js`) only reapplies the visually-hidden live-region
counter after each swap — it never filters cards in the browser.

## Sub-features

- `my-cases-cards-parity`: page title `Todos os seus encaminhamentos`
  via `page_title`, integral container (`operational-list-page`), no
  top `Enviar relatório` CTA and no internal pills; neutral header card
  (`Meus Casos` + badge of the filtered total + update note, no
  `alert-warning`); cards `.case-card > .row` in four desktop regions
  4/3/3/2 (identity/procedures | received | status/alerts | `Ver
  detalhes` CTA), `.patient-name`, demography/suffix in metadata, no
  `summary-box`/`waiting-time`/`list-group-item`.
- `my-cases-server-filters`: `q=VERIFY-001` narrows to the matching card
  (server round-trip); `q` with 2 chars is ignored (never zeroes the
  list); `status` and `procedure_type` narrow by state/declared type;
  combined filters compose by AND; header tab switch preserves filters.
- `my-cases-creator-scope`: a foreign case matching every filter
  (`VERIFY-X01`, created by another NIR) never appears — in the full
  page or in the partial.
- `my-cases-exceptions-empty`: `FAILED` badge, retention badge and
  administrative closure (result + reason) stay compact; empty states
  are centred — with filters (`Limpar filtros`), without filters per
  tab (`Novo Encaminhamento`).
- `my-cases-auto-refresh-partial`: the container `#my-cases-content`
  polls `cases/partials/list/` every 20 s (`hx-trigger="every 20s"`),
  with the active filters in the poll URL; a compatible new case
  (`VERIFY-M10`, `FAILED` + `art_perif`) appears without reload after
  the `htmx:afterSwap` with the tab and the filters (`FAILED` +
  `art_perif`) still applied; an off-filter new case (`VERIFY-M11`,
  status `NEW`) never appears after the swap.
- `my-cases-geometry`: at 1280 px the four regions share a row and the
  container aligns with the header; at 390 px they stack, badges wrap,
  `Ver detalhes` is full-width with a ≥44 px target and there is no
  horizontal overflow. Every capture waits for `.app-shell` computed
  opacity ≥0.99 (no fixed sleep).

## How to get to it (user POV)

- Log in as a NIR (real form) — the dispatcher lands on
  `/intake/cases/?tab=active`.
- Read the cards under *Todos os seus encaminhamentos*; use *Buscar por
  número de ocorrência*, *Filtrar por status* and *Filtrar por tipo de
  procedimento*; switch between *Meus Casos* and *Casos Encerrados* in
  the header (filters are preserved).
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
asserts the parity markers + htmx wiring + single header navigation,
drives the server filters (q, short q, status, procedure type, AND),
proves header tab round-trip with preserved filters, the empty states
and the desktop/mobile geometry, then runs the refresh checks with the
filters (`status=FAILED` + `procedure_type=art_perif`) active: creates
one compatible synthetic case (`VERIFY-M10`) through the verify
settings as setup, waits for the 20 s poll response and the
`htmx:afterSwap` of the container and asserts the new card appears
without reload with tab/filters preserved, then asserts an off-filter
new case (`VERIFY-M11`) never appears after the swap — and snapshots
each step (`nir-meus-casos-*.png/.txt`) under `<run-dir>/artifacts/`.
Exit codes: `0` PASS, `1` observed application failures, `2`
infrastructure/driver blocker (never switch engines).

## Gotchas

- The seed already creates `VERIFY-001`/`VERIFY-002` (created by
  `verify-nir`, `AWAITING_DOCTOR`) — the driver only adds
  `VERIFY-M01` (own, `FAILED`), `VERIFY-M02` (own, `CLEANED`,
  closed tab), `VERIFY-M03` (`cat_cardiaco` only), `VERIFY-M04`
  (own, administrative closure via the real service, closed tab),
  `VERIFY-M05` (own, retained for review) and the foreign `VERIFY-X01`
  (another NIR) before login. A dedicated empty NIR
  (`verify-nir-vazio`, no cases) gets the synthetic seed password so
  the driver can prove the no-filter empty states through the real
  form.
- The poll is 20 s: the refresh steps wait for the partial response
  and for the `htmx:afterSwap` of `#my-cases-content` before asserting
  the DOM (the HTTP response alone does not prove the swap), they
  never force a reload. A missing poll or swap means the htmx wiring
  regressed — report FAIL, never reload to make it pass.
- Every screenshot waits for `.app-shell` opacity ≥0.99; the mobile
  stability check also compares the card height across a double
  `requestAnimationFrame` after that point.
- Cleanup drops the whole database, so no state leaks between runs.
