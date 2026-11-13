# Fila médica em leitura

The doctor queue (`/doctor/`) lists cases awaiting a medical decision as
cards — patient, agency record number, declared procedures with subtype
badges — and the first card opens the case work page for reading. The
initial journey proves **reading only**: no decision is submitted.

## Sub-features

- `queue-heading-tabs`: heading *Fila médica* with the *Aguardando decisão*
  (default) and *Decididos* tabs.
- `queue-lists-synthetic-case`: the seeded `VERIFY-001` card shows
  *Paciente Sintético Um*, the record number and the declared procedure
  labels/badges.
- `queue-card-opens-work-page`: clicking the *Aguardando* card navigates to
  `/doctor/case/<id>/decide/` (the work page with the clinical context and
  the decision form at the end).
- `queue-empty-state`: with no awaiting cases the queue shows *Nenhum caso
  aguardando decisão médica.* (not covered by the seed — documented only).

## How to get to it (user POV)

- Log in as a doctor (or open `/` — the dispatcher sends doctors to the queue).
- Read the cards under *Aguardando decisão*; click a card to open its work
  page.

## Driving it with Playwright

Same drive as [login.md](login.md): after the doctor login, assert the queue
heading, assert the body text contains `VERIFY-001` and *Paciente Sintético
Um*, snapshot (`doctor-queue.png/.txt`), click the first
`a.list-group-item-action` card, assert the URL matches
`/doctor/case/<id>/decide/`, snapshot (`doctor-case-read.png/.txt`). The
drive stops at reading — it never fills or submits the decision form.

## Gotchas

- Opening the work page claims the case lock (`doctor_decision` context) —
  that is the app's normal reading behavior, not a decision. Cleanup drops
  the whole database, so no lock state leaks between runs.
- The seeded cases are setup, never clinical or decision proof. A missing
  `VERIFY-001` card means the seed or the queue filter regressed — report
  FAIL, never seed again mid-drive.
- The *Decididos* tab, subtype filter and pagination are mapped but not
  driven by the initial journey; do not claim them verified.
