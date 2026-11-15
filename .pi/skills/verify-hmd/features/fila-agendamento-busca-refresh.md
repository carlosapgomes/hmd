# Fila do agendador — busca client-side e auto-refresh (ui-parity-queues slice 002; OLI-002)

Journey `fila-agendamento-busca-refresh`: the scheduler queue
(`/scheduler/`, heading *Solicitações de agendamento aguardando
confirmação*, cards `case-card`) with a server-side `procedure_type`
filter (preserved in form/pagination/`hx-get`/partial) + client-side
search by name/occurrence (no reload, no request), lock states
reflected on the cards (Agendar / Continuar / Reservado + holder) and
htmx auto-refresh every 20 s from `scheduler:queue_partial` (same
fragment, same guards) re-applying the active search + server filter.
The queue is complete — no unit differentiation (both scheduled units'
cases are listed together on the processed tab; `unit_label` is display
only). The processed tab preserves its presentation (`Recebido há`,
unit, `status-badge`) outside the pending redesign.

## Sub-features

- `queue-cards-parity`: pending cards `.case-card > .row` with content
  `col-lg-8` and time/action `col-lg-4`, `summary-box + waiting-time`,
  **no** generic `status-badge`, **no** `list-group-item`, single
  `Aguardando há` per card, patient name + age, occurrence `VERIFY-S01`,
  `Dias em tela: 3`, declared procedures with subtype + medical
  disposition badges (mixed approved/denied in one case), the count
  alert `alert-info` with `pulse-dot` (computed style: 8×8, danger
  token, `pulse-dot` animation), full container (no `col-lg-10`) and
  page 1 full (20 cards); no legacy `nav-tabs` block (navigation is
  the `_nav` pills `Pendentes/Processados` with count badges).
- `queue-lock-states`: free card offers the real primary `Agendar`
  link with `stretched-link`, the scheduler's own lock offers
  `Continuar`, and another scheduler's lock shows the disabled
  `Reservado` button with the holder name and no link/stretched-link.
- `queue-procedure-filter-server-side`: the `procedure_type` form
  filters on the server before pagination (`cat_cardiaco` → only
  `VERIFY-S01` in the HTML; `art_perif` pagination links preserve the
  filter; the partial `hx-get` carries the filter); an invalid value
  is ignored (full queue, no error); the processed tab has no new
  filter.
- `queue-search-without-reload`: typing the patient name or the
  occurrence number in the search input hides non-matching cards in
  place (same URL, no navigation); the ARIA status
  (`data-scheduler-queue-filter-status`, `aria-live="polite"`) reports
  the visible count and the no-results alert appears for a non-matching
  term; clearing restores all cards.
- `queue-auto-refresh-partial`: the container `#scheduler-queue-content`
  polls `partials/queue/` every 20 s (`hx-trigger="every 20s"`) **under a
  valid `procedure_type=art_perif` filter** (after the invalid-value proof);
  the driver enters the term `VERIFY-S03` (matching only the new case)
  before the poll, and after the swap asserts the valid filter is still in
  the URL and `hx-get` (and in the partial response URL), the term is
  still in the input, the matching new case (`VERIFY-S03`) is visible, a
  non-matching card (`Paciente Sintético Agendamento Um`) stays hidden
  (exactly one visible card), the other-type case (`VERIFY-S30`,
  `cat_cardiaco` only) is absent from the fragment, and the out-of-page
  case (`VERIFY-S99`, null `days_on_screen`, last position) never reaches
  the swapped fragment — all without reload (URL unchanged from pre-poll).
  Applying the search under an active server filter is the
  `htmx:afterSwap` reapplication.
- `queue-processed-preserved`: the processed tab keeps `Recebido há`
  (never `Aguardando há`), the scheduled unit (`Unidade 2`) and the
  `status-badge`, with no procedure filter, captured in
  `scheduler-queue-processados.png`.
- Geometry at 1280/390: header/main alignment, 8/4 side-by-side on
  desktop, stacked on mobile, CTA ≥44 px, no horizontal overflow
  (`fila-agendamento-oli002-measures.json`).

## How to get to it (user POV)

- Log in as a scheduler (real form) — the dispatcher lands on
  `/scheduler/`.
- Filter by procedure type (server-side) and/or type in *Buscar por
  nome ou ocorrência*; watch the cards filter in place.
- Leave the page open: the list refreshes by itself every ~20 s, keeping
  the filter and search.

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
occurrence, then a non-matching term, then clear), submits the server
filter (`cat_cardiaco` → single card, `art_perif` pagination preserved,
invalid ignored), creates one compatible synthetic case (`VERIFY-S03`)
through the verify settings as setup, enters `VERIFY-S03` in the search
before the 20 s poll **under the valid `art_perif` filter**, waits for
the poll and asserts the filter + term are preserved, the matching new
card is visible, a non-matching card stays hidden, the other-type case
is absent from the fragment and the out-of-page case is absent — all
without reload — captures the processed tab (after the `.app-shell`
fade-in is stable) and the mobile layout, and snapshots each
step (`scheduler-queue-busca-*.png/.txt`,
`scheduler-queue-filtro-server.*`, `scheduler-queue-processados.*`,
`scheduler-queue-mobile.*`) under `<run-dir>/artifacts/`.
Exit codes: `0` PASS, `1` observed application failures, `2`
infrastructure/driver blocker (never switch engines).

## Gotchas

- The seed only creates `AWAITING_DOCTOR` cases, so the driver creates
  the scheduler fixtures itself (processing + doctor approval to
  `SCHEDULER_REQUESTED`): `VERIFY-S01` (mixed approved/denied,
  `Dias em tela: 3`), `VERIFY-S02`, 17 filler cases (page 1 full),
  `VERIFY-S10` (own lock → `Continuar`), `VERIFY-S11` (another
  scheduler's lock → `Reservado` + holder, no action), `VERIFY-S99`
  (null `days_on_screen`, out of page 1) and `VERIFY-S20` (confirmed,
  unit 2, for the processed tab) before login; `VERIFY-S30`
  (`cat_cardiaco` only, days_on_screen=9) is created before the polling
  stage to prove exclusion of another-type cases from the swapped
  fragment under the valid `art_perif` filter.
- The poll is 20 s: the refresh step waits for the partial response,
  it does not force a reload. A missing poll means the htmx wiring
  regressed — report FAIL, never reload to make it pass.
- Opening a card claims the case lock (`scheduler_confirm`) — the
  journey never clicks a card (lock fixtures are claimed directly in
  the setup), so no lock is claimed by navigation. Cleanup drops the
  whole database, so no state leaks between runs.
