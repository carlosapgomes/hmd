---
name: verify-hmd
description: "Drive the SAR – HMD Django app through the real browser with Playwright: isolated loopback instance, own hmd_verify_* database, synthetic seed and real-form login. Use to prove login, per-role home and medical-queue reading, capture snapshots/PNG/JSON evidence and distinguish application failures from harness failures; not a substitute for pytest or for institutional AD proof."
disable-model-invocation: true
---

# Verify SAR – HMD through the real browser

Read [features/README.md](features/README.md) before driving. This skill is for an
agent with no conversation context. Working directory is the HMD checkout.
Helpers live beside this skill, never in the production app. Shell + Node only
(zero Python outside `apps/accounts/management/commands/seed_verify.py`).

## Proof standards (read first)

- Real form login only. Injected cookies, `TestClient`-as-browser and test-only
  mutation routes are **never** proof. The drive script contains no `addCookies`.
- The synthetic login backend (`apps.accounts.backends_verify`) authenticates
  the seeded role users through the real form; it refuses to load under any
  settings other than `config.settings.verify` (negative tests below).
- Chrome DevTools MCP, if used at all, is diagnosis only — never proof. It is the diagnostic fallback only: if the pinned Chromium download fails, use Chrome MCP to inspect the page/network for diagnosis, never as substitute Chromium proof, and report BLOCKED with the Chromium log.
- Evidence survives cleanup: `tmp/verification/<run>/artifacts/` is preserved
  (only the server process and the `hmd_verify_*` database are removed).

## Launch

Prerequisites observed in this repo: Python 3.13+ with the project `.venv`,
PostgreSQL reachable at `127.0.0.1:$VERIFY_DB_PORT` (default honors
`$TEST_DB_PORT`, else `55435` — the host port of the `docker-compose.test.yml`
PostgreSQL, user `hmd`), `psql`/`createdb`/`dropdb`, Node 18+. The browser
is the **pinned Playwright Chromium** (`1.64.0`) — never the system Chrome.
Restore it and download that Chromium (the download is part of the proof
setup, retained in `artifacts/playwright-install.log`):

```bash
npm ci --prefix .pi/skills/verify-hmd/scripts/browser --ignore-scripts --no-audit --no-fund
.pi/skills/verify-hmd/scripts/browser/node_modules/.bin/playwright install chromium
```

If the Chromium download fails (no network), STOP and report **BLOCKED**
with that download log as evidence — never silently substitute another
engine (system Chrome, Chrome MCP). `control-hmd drive` re-runs the install
step on every drive and fails the same way.

Launch from the checkout:

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
```

Stdout is the exact run directory under the checkout's `tmp/verification/`
(gitignored). Persist THAT identity, never guess the latest directory. The
helper creates a NEW `hmd_verify_<hex>` database, runs migrations + the
synthetic seed (`seed_verify`), and starts the real `manage.py runserver` on a
dynamically allocated loopback port (`18765–18850`) under
`config.settings.verify`. The manifest (`manifest.json`) records run id, PID +
start ticks, boot id, loopback URL, database, build SHA and the public
fictional credentials. If the launch fails midway, it cleans up its own
database before exiting.

The launch also runs `collectstatic` so the isolated instance serves the
real static assets (`/static/css/app.css`, `/static/js/sw.js`, …) through
WhiteNoise with `DEBUG=False` — the same assets the app serves.

Seeded identities (public, fictional — in the manifest, never a secret):

- `verify-nir`, `verify-doctor`, `verify-scheduler`, `verify-manager`,
  `verify-admin` — one role each (`verify-admin` is also superuser/staff).
- Password for all: `hmd-verificacao-ficticia-2026`.
- Two synthetic cases in `AWAITING_DOCTOR`: `VERIFY-001` (Paciente Sintético
  Um) and `VERIFY-002` (Paciente Sintético Dois), with declared procedures.

`config.settings.verify` is fail-closed: loopback host only, database name
must match `hmd_verify_*` and contain no `prod`; `DATABASE_URL`/`DB_*` of
dev are ignored by construction. Never start/stop the dev server, the test
compose or any other project's stack. Never point `VERIFY_DB_*` at a dev/prod
database — the settings refuse it at import.

## Doctor

The single read-only check:

```bash
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <run-dir>
```

Exit 0 means the owned instance is worth driving. It verifies, without any
mutation: manifest ownership (directory under `tmp/verification/`, run id =
directory name, `hmd_verify_*` database), the manifest URL is **exactly**
`http://127.0.0.1:<manifest port>` (the exact origin the driver will use —
any mismatch refuses), PID alive with matching start ticks and boot id
(PID-reuse protection), the process command line shows
`config.settings.verify` and the manifest port, the process environment
carries this run's per-run marker and build identity (`HMD_VERIFY_MARKER` /
`HMD_VERIFY_BUILD`, recorded in the manifest at launch), **the PID owns the
listening socket** on that port (socket inode vs `/proc/<pid>/fd` — a
matching command line alone does not prove socket ownership), the current
build identity (HEAD + dirty-tree hash) matches the manifest, `/healthz/`
answers `{"status": "ok"}` and the login form at `/login/` renders with the
`Entrar` button. Anything off → exit 2, do not drive.

## Drive

The Playwright runner owns doctor + browser + evidence (not launch/cleanup —
run those separately as above). **Every** drive goes through `control-hmd
drive`, which runs doctor first and then installs/launches the pinned
Playwright Chromium. Invoking `drive.mjs` directly is not a supported entry
point (it would bypass doctor):

```bash
.pi/skills/verify-hmd/scripts/control-hmd drive --run <run-dir>
```

It performs the proven journey end to end in isolated browser contexts:
real-form login as `verify-doctor` → home is the medical queue (`/doctor/`,
heading *Casos aguardando decisão*, synthetic card `VERIFY-001` visible) → opens the case
work page (`/doctor/case/<id>/decide/`, read-only — no decision is submitted)
→ real-form logins as `verify-nir` (`/intake/`, *Envio de relatório de
regulação*), `verify-scheduler` (`/scheduler/`, *Solicitações de agendamento aguardando confirmação*),
`verify-manager` and `verify-admin` (`/dashboard/`, *Painel gerencial*).

Selectors come from real snapshots: `getByLabel('Usuário')`,
`getByLabel('Senha')`, button `Entrar`, headings by accessible name. The drive
also fetches the required static assets (`/static/css/app.css`,
`/static/js/sw.js`) and fails if any asset answers non-200 or any `/static/*`
response during the journey is ≥ 400 — screenshots without the real assets
are not visual evidence. Exit codes: `0` PASS, `1` observed application
failures, `2` infrastructure/driver blocker (e.g. pinned Chromium
unavailable — **BLOCKED**, never switch engines silently).

## Evidence

Artifacts survive under `<run-dir>/artifacts/`:

- `report.json` — every check id with PASS/FAIL.
- `observed-state.json` — run id, base URL, database, per-step URLs.
- `<step>.png` + `<step>.txt` — full-page screenshot and accessibility
  snapshot (URL + ARIA tree) per step, including the login form *before* the
  action and the resulting page.
- `migrate.log`, `seed.log`, `server.log`, `cleanup.json`.

Proof standards: browser action AND resulting page are recorded (a final
screenshot alone is insufficient); session proof is the authorized landing
page after the real form POST; seed operations are setup, never feature proof.
After cleanup, confirm the evidence files still exist before reporting.

## Cleanup

```bash
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <run-dir>
```

Cleanup verifies boot identity + PID start ticks (never kills by process
name, never signals a reused PID), terminates only the process it started,
drops only its own `hmd_verify_*` database **at the endpoint recorded in
the manifest** (`dbHost`/`dbPort`, validated — never the current
environment), propagates `dropdb` failures (no blanket success), confirms
the removal in `pg_database` and only then writes `cleanup.json` with
`databaseDropped: true`. A failed cleanup writes `databaseDropped: false`
with the error and exits non-zero — fix the cause and re-run cleanup with
the same manifest to retry. Evidence is never removed. Run cleanup after
every failed iteration too; launch already cleans up its own startup
failures.

## Negative tests (R1b + fix-1 guards)

Pytest negatives (subprocess, no database):
`apps/accounts/tests/test_verify_guards.py` — `seed_verify` refused under
dev and prod-like settings before any ORM (unreachable port proves no
connection is attempted); `backends_verify` refused with empty
`DJANGO_SETTINGS_MODULE`, under dev settings, and under programmatically
configured settings (`settings.configure()`) even with an `hmd_verify_*`
database name (fail-closed without positive verify identity).

Shell negatives (run through the harness):

```bash
# Config de verificação recusa banco prod-like e host não-loopback:
VERIFY_DB_NAME=hmd_prod DJANGO_SETTINGS_MODULE=config.settings.verify uv run python -c "import django; django.setup()"
VERIFY_DB_HOST=db.example.internal DJANGO_SETTINGS_MODULE=config.settings.verify uv run python -c "import django; django.setup()"
# (ambos exit 1, ImproperlyConfigured no import)

# Launch recusa host não-loopback ANTES de qualquer psql:
VERIFY_DB_HOST=db.example.internal .pi/skills/verify-hmd/scripts/control-hmd launch
# (exit 2, validação do endpoint — nenhum psql tentado; e createdb nunca
# é precedido de dropdb: colisão de nome falha fechado)

# Doctor recusa origem divergente (manifest.url != http://127.0.0.1:<port>)
# e ouvinte alheio (PID sem o marcador da run / sem posse do socket):
# copiar uma run válida, adulterar `url` para outra porta → doctor exit 2;
# apontar o manifest para um processo com cmdline compatível mas sem
# HMD_VERIFY_MARKER nem posse do socket LISTEN → doctor exit 2.

# Cleanup falho + retry: com o endpoint do manifest inalcançável, cleanup
# sai != 0 com databaseDropped=false (nunca sucesso em branco); restaurado
# o endpoint, o retry com o mesmo manifest confirma a remoção e sai 0.
```

The dev database is never touched: the launch only ever creates/drops
`hmd_verify_*` on loopback, and `pg_database` before/after shows no other
database added or removed.

## Helpers

- `scripts/control-hmd`: executable shell CLI; `launch`, `doctor --run`,
  `drive --run`, `cleanup --run` as invoked above. No arbitrary SQL, no
  production URL argument.
- `scripts/browser/drive.mjs`: executable Playwright runner (pinned 1.64.0,
  pinned Chromium — only via `control-hmd drive`, never invoked directly).
  The only supported journey is the one described in Drive.
- `scripts/browser/package.json` / `package-lock.json`: pinned Playwright;
  restore with the `npm ci` command under Launch.

For keeping selectors, feature entry points and proof standards current,
update the map in `features/` whenever UI/routes/authentication change.
