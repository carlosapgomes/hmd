# Fila médica — decididos orientados ao resultado (operational-lists-ui-parity slice 003)

Journey `fila-medica-decididos`: the doctor queue decided tab (`/doctor/?tab=decididos`,
`Casos decididos`, full container, card `.case-card > .row` 8/4) shows a
result-oriented card per case — identity/demography, each declared procedure
with label/subtype, disposition badge (`Aprovado` green / `Negado` red) and
`doctor_decided_at` (`Decidido em d/m/Y H:i`), with no waiting/received
time and no case-level `status-done` badge — plus a real outline
`Ver detalhes` CTA (`stretched-link`) to the read-only `doctor:case_detail`.
The single server-side `?subtype=` filter shows options **without** pending
counts in this tab, and the empty state is a single `alert-info`. Geometry is
measured at 1280 and 390 px into `fila-medica-oli003-measures.json`.

## Sub-features

- `decided-heading-filter`: heading *Casos decididos* (no *Decididos Hoje*,
  no date cut), single `select[name="subtype"]` whose options carry no
  counts (`Todas` without `(N)`), no `Pendentes por subtipo` row, no legacy
  `nav-tabs` (navigation is the `_nav` pills `Pendentes/Decididos`).
- `decided-mixed-result-card`: the mixed synthetic case (`VERIFY-D01`)
  shows both rows — *Arteriografia periférica* (angio, `Aprovado`,
  `text-bg-success`, `Decidido em …`) and *Cateterismo cardíaco* (cardio,
  `Negado`, `text-bg-danger`, `Decidido em …`) — with two distinct decision
  times, identity/demography (`Paciente Sintético Decidido Misto`, `66 a`,
  `VERIFY-D01`), zero `.waiting-time`, zero `status-badge`, zero
  `Aguardando há`/`Recebido há`/`Recebido em` and zero `list-group-item`.
- `decided-cta-read-only`: `Ver detalhes` (`btn-outline-primary
  stretched-link`, >=44 px) points to `/doctor/case/<id>/` (detail, never
  `/decide/`); clicking it lands on the read-only detail showing the
  recorded decisions.
- `decided-search-and-server-filter`: client-side search filters the decided
  cards in place; the server filter `subtype=angio` removes the cardio-only
  decided card from the HTML while the mixed card stays.
- `decided-empty-info`: `?tab=decididos&subtype=radio` (no decided case
  declares a radio type) shows a single `alert-info` (*Nenhum caso decidido
  …*) with no card.

## How to get to it (user POV)

- Log in as a doctor (real form) — the dispatcher lands on `/doctor/`.
- Open the *Decididos* pill and read the result cards; click
  *Ver detalhes* to open the read-only case detail.

## Driving it with Playwright

Standalone driver (same ownership/login standards as the other drives —
real form login, pinned Chromium, owned loopback instance only):

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <run-dir>
node .pi/skills/verify-hmd/scripts/browser/drive-fila-medica-decididos.mjs --run <run-dir>
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <run-dir>
```

The driver creates the decided fixtures (`VERIFY-D01` mixed approved+denied
with distinct `doctor_decided_at` per row, `VERIFY-D02` denied-only,
`VERIFY-D03` approved-only) through the verify settings before login, logs
in as `verify-doctor` through the real form at 1280px, asserts the result
markers + measures, clicks `Ver detalhes` to the read-only detail, drives
the client-side search and the server filter, proves the empty `alert-info`
state, measures a 390px mobile context (stacked regions, CTA >=44px, no
overflow) and snapshots each step (`doctor-decididos-*.png/.txt`,
`doctor-case-decidido-detalhe.png/.txt`) plus
`fila-medica-oli003-measures.json` under `<run-dir>/artifacts/`.
Exit codes: `0` PASS, `1` observed application failures, `2`
infrastructure/driver blocker (never switch engines).

## Gotchas

- The two seeded cases are `AWAITING_DOCTOR` and never appear in *Decididos*:
  without the fixtures the tab would be empty, so the fixtures are setup,
  never decision proof. A missing `VERIFY-D01` card means the seed or the
  queue filter regressed — report FAIL, never seed again mid-drive.
- The decided rows' times are set per row after the service call so the
  mixed card proves two distinct `Decidido em …` values.
- Opening the detail does not claim a lock (only the decide page does).
  Cleanup drops the whole database, so no state leaks between runs.
