# Fila médica — busca client-side e auto-refresh (ui-parity-queues slice 001)

Journey `fila-medica-busca-refresh`: the doctor queue (`/doctor/`, cards
`case-card`) with client-side search by name/occurrence (no reload, no
request), exam/subtype client refinement over the server `?subtype=`
scope, and htmx auto-refresh every 20 s from `doctor:queue_partial`
(same fragment, same guards) re-applying the active search/filter.

## Sub-features

- `queue-cards-parity`: cards `case-card + summary-box + waiting-time +
  status-badge`, patient name + age (`64 a`), occurrence `VERIFY-001`,
  `3 d em tela`, declared procedures with subtype badges, the alert
  `pulse-dot` (computed style: 8×8, danger token, `pulse-dot` animation)
  and page 1 full (20 cards); no legacy `nav-tabs` block (navigation is
  the `_nav` pills `Pendentes/Decididos` with count badges).
- `queue-search-filters-without-reload`: typing the patient name or the
  occurrence number in the search input hides non-matching cards in
  place (same URL, no navigation); the ARIA status
  (`data-doctor-queue-filter-status`, `aria-live="polite"`) reports the
  visible count and the no-results alert appears for a non-matching
  term; clearing restores all cards.
- `queue-exam-filter-refines`: the exam filter (`data-doctor-exam-filter`,
  options from the user's subtypes) only hides cards outside the
  selected subtype — it never lists beyond the server scope. Proven with
  single-subtype fixtures created by the driver as setup (angio-only
  `VERIFY-010` visible, cardio-only `VERIFY-011` hidden under `angio`).
- `queue-auto-refresh-partial`: the container `#doctor-queue-content`
  polls `partials/queue/` every 20 s (`hx-trigger="every 20s"`); with
  the non-default exam filter (`angio`) still active through the swap, a
  compatible new case (`VERIFY-003`, angio-only) appears without reload
  after the poll, the out-of-filter case stays hidden and the
  out-of-page case (`VERIFY-099`, null `days_on_screen`, last position)
  never reaches the swapped fragment.

## How to get to it (user POV)

- Log in as a doctor (real form) — the dispatcher lands on `/doctor/`.
- Read the cards under *Pendentes*; type in *Buscar por nome ou
  ocorrência*; watch the cards filter in place.
- Leave the page open: the list refreshes by itself every ~20 s.

## Driving it with Playwright

Standalone driver (same ownership/login standards as the other drives —
real form login, pinned Chromium, owned loopback instance only):

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <run-dir>
node .pi/skills/verify-hmd/scripts/browser/drive-fila-medica-busca-refresh.mjs --run <run-dir>
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <run-dir>
```

The driver logs in as `verify-doctor` through the real form, asserts
the parity markers (incl. the computed `pulse-dot` style) + htmx
wiring, drives the search (name, then occurrence, then a non-matching
term, then clear), creates the single-subtype fixtures (see Gotchas)
through the verify settings before login, drives the exam filter
(`angio`, kept active through the poll), creates one compatible
synthetic case (`VERIFY-003`) through the verify settings as setup,
waits for the 20 s poll and asserts the new card appears without
reload with the filter re-applied while the out-of-filter case stays
hidden and the out-of-page case is absent, and snapshots each step
(`doctor-queue-busca-*.png/.txt`) under `<run-dir>/artifacts/`.
Exit codes: `0` PASS, `1` observed application failures, `2`
infrastructure/driver blocker (never switch engines).

## Gotchas

- The two seeded cases both declare `art_perif` (angio) + `cat_cardiaco`
  (cardio), so they alone cannot discriminate subtypes: the driver
  creates single-subtype fixtures (`VERIFY-010` angio-only,
  `VERIFY-011` cardio-only), 16 filler angio cases (page 1 full) and
  `VERIFY-099` (null `days_on_screen`, out of page 1) before login; the
  `angio` exam filter stays active through the 20 s poll (never reset
  to `all`) and the refresh asserts cover the compatible case, the
  out-of-filter case and the out-of-page case.
- The poll is 20 s: the refresh step waits for the partial response,
  it does not force a reload. A missing poll means the htmx wiring
  regressed — report FAIL, never reload to make it pass.
- Opening a card claims the case lock (`doctor_decision`) — the journey
  never clicks a card, so no lock is claimed. Cleanup drops the whole
  database, so no state leaks between runs.
