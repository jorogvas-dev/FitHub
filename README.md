# FitHub School

Fitness application with PostgreSQL and ChromaDB, replacing the earlier document-focused starter.

## Windows launch

For an existing installation, keep its `.env` and database volumes and run:

```powershell
Set-Location E:\FitHub-School
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\update-deployment.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-deployment.ps1
```

The update checks port availability and preserves the `fithub-school` project name,
all existing environment values, secrets, credentials and data volumes. It adds only
missing HTTPS/log port settings. No unrelated services are stopped. See the
[deployment guide](./docs/deployment.md) for architecture, certificate handling,
health checks, failover verification and custom ports.

Open https://localhost:8443 and accept the local self-signed certificate warning.
HTTP http://localhost:8010 redirects to HTTPS. Container logs: http://localhost:8088.
For a new installation only, use `scripts/setup.ps1`; it generates secrets only when
`.env` does not exist. No default login credentials exist. Registration creates regular
member accounts. Administrator access requires explicit promotion of an existing account;
there is no default administrator password. See [phase 1](./docs/phase1-ui-onboarding.md).

```powershell
docker compose exec db psql -U fithub -d fithub -c "\dt"
```

Expected: 19 tables including the migration ledger. Health endpoint: https://localhost:8443/health.

## Implemented in this version

- Fitness branding and responsive pages.
- Grouped member sidebar/mobile navigation and an account-scoped training, nutrition and bodyweight dashboard; see [approved theme, checks and screenshots](./docs/black-violet-theme.md).
- Register/login/logout, hashed passwords, form CSRF tokens and protected dashboard.
- User-scoped dashboard counts for workout plans, sessions and latest weight.
- Searchable exercise catalogue with 68 seeded exercises, including correctly labelled timed movements.
- Registration onboarding that fills missing profile details and preserves existing measurements.
- Protected exercise administration with create/edit/deactivate/reactivate controls.
- Private workout plans, days and exercise prescriptions with create/edit/delete forms.
- Start workouts from training days; log, edit and delete sets with reps, optional weight and RIR.
- Private workout history, preserved plan targets, and read-only finished sessions.
- Private fitness profiles and dated bodyweight history with add/edit/delete forms.
- Private nutrition preferences, user-entered targets and a manual food diary with saved-food snapshots.
- Shared offline USDA food catalogue, private recipes and rule-based four-meal drafts with explicit logging.
- Explicit unknown nutrition values, incomplete/over-target totals and user-local diary dates.
- Latest bodyweight and signed change from the previous measurement on the dashboard.
- nginx local HTTPS/HTTP redirect, two identical web replicas and Docker DNS recovery.
- Project-filtered Dozzle logs and health-based startup dependencies.
- Fitness schema and persistent PostgreSQL/ChromaDB volumes.

## Next

Timed exercise targets. Workout logging is available from each training day, and the dashboard links to workout history, fitness profiles and bodyweight history. ChromaDB is running but has no fitness knowledge indexed yet; there is no AI endpoint or model loaded in this version. No PDFs or document analysis features.
The full UML diagram remains future school-documentation work. Login has no rate limiting/password reset/email verification yet; this local school deployment is not ready for public hosting.

## Structure

app/routes/auth.py: authentication
app/routes/main.py: home, protected dashboard, exercises
app/routes/profile.py: private fitness profiles and bodyweight tracking
app/routes/nutrition.py: private preferences, saved foods and manual food diary
app/services/health.py: database health
deploy/: nginx TLS/proxy configuration and Chroma heartbeat check
app/templates/: web pages
app/static/: stylesheet
db/init/01-schema.sql: schema and exercise seed data
scripts/: first-start scripts
tests/: auth and access regression checks
docs/: design and validation notes

## Stop / restart

`docker compose stop` stops the project without removing containers or volumes.
`docker compose up -d --build --wait` restarts the complete deployment with two web replicas.
Initialization SQL only runs with an empty database volume. This starter uses fresh FitHub volumes and does not migrate data from the old document schema.

Dependencies are pinned in requirements.lock.txt (Linux/Python 3.12); use Docker on Windows.

## Updating from v0.1

See README-WORKOUT-UPDATE.md for the historical workout editor update.

That describes the older workout editor update. The current phase 1 update requires
the additive migrations in [the phase 1 guide](./docs/phase1-ui-onboarding.md).
The deployment updater applies them automatically before replacing web containers.

## Workout logging update

For a current installation, migrate and rebuild the web service from PowerShell:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose up -d --no-deps --wait web
docker compose ps
```

Keep the existing environment and database volumes. Do not rerun setup or remove volumes.
No additional Python dependencies are required. Phase 1 uses versioned, additive
schema migrations; existing records and IDs are retained.

Open a plan and select **Start workout** on a training day. Log sets using an exercise
from the catalogue, a positive set number, 0–1000 reps, optional weight of 0–10000 kg
(up to two decimal places), and optional RIR of 0–10. Blank weight means bodyweight;
zero weight remains an explicit zero. Set numbers must be unique per exercise within
each session. Finish the workout when done; finished sessions cannot be changed.

The session records its original plan/day names and prescribed exercises/targets as a
versioned JSON snapshot in the existing `workout_session.notes` text field. Existing
session notes are never rewritten. Older sessions use their current day when available.
Set details join the exercise catalogue independently, so historical exercise names
remain viewable after day or plan deletion. The navigation and dashboard link to history.
All mutations require POST and CSRF. Session row locks serialize set mutations and
completion; completion sets its timestamp once.

Run all checks using the existing Docker dependencies and PostgreSQL:

```powershell
Set-Location E:\FitHub-School
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

Integration tests create a unique temporary PostgreSQL schema and remove only that
schema afterwards. Accounts, plans and logs in the application schema are untouched.
The tests cover authentication/CSRF, full plan and workout flows, ownership and parent
IDs, invalid inputs, duplicate insert/edit rollback, historical sessions, idempotent
completion, and concurrent add/edit/delete requests waiting on a completion lock.
Profile/bodyweight checks cover CRUD, optional fields, enum/decimal/date validation,
duplicate-date rollback, ownership/guessed IDs, deletion confirmation, data preservation,
and dashboard changes for zero, one or multiple measurements (including unchanged weight).
Without `FITHUB_TEST_DATABASE_URL`, PostgreSQL tests are skipped. No separate linter
or type-check configuration is present in this project.

## Fitness profile and bodyweight

Use **Profile** in the navigation to save height, goal and experience. All fields are
optional and can be cleared. Height accepts 50–300 cm with up to one decimal place;
goals and experience levels come from the existing database choices.

Use **Bodyweight** to view your measurements, newest date first, and add or edit a
dated entry. Weight accepts finite plain decimals between 20 and 500 kg with up to
two decimal places. Dates must be valid `YYYY-MM-DD` dates no later than the server's
current date. Duplicate dates show a message and preserve both existing entries.
Deletion opens a confirmation form and requires its checkbox and CSRF token.

The dashboard shows the latest measurement by date, with its signed change from the
previous recorded measurement. With one entry it asks for another measurement; with
no entries it shows an empty state. A change of zero is displayed as `0.00 kg`.
Profiles and measurements are private; request fields cannot choose another owner.

Use the web-only PowerShell rebuild and Docker test commands above for this update.
The existing schema, dependencies, accounts, plans and workout logs are preserved.

## Manual nutrition tracking

Use **Nutrition** in the navigation for the daily diary, saved foods, separate allergy
notes/preferences, manual targets and IANA timezone settings. Unknown values remain
unknown; saved-food edits never change earlier diary snapshots. The shared library
bundles 100 attributed USDA foods for offline use. Private recipes support bounded,
rule-based four-meal drafts, review, swaps and explicit duplicate-protected logging.
No automatic calorie prescription, AI, external food API or barcode integration is included.
See [the nutrition guide](./docs/nutrition.md) for migration/rebuild commands, input
bounds, calculation rules, tests and a manual checklist. The versioned updater applies
`003_nutrition`, `004_food_catalogue`, `005_meal_planner` and `006_usda_source_links` before replacing web containers; no reset or new dependency is needed.
