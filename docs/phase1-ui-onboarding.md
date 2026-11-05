# Phase 1: UI, onboarding and exercise administration

## Upgrade the existing installation

With the existing database and Chroma services running, use PowerShell:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose up -d --no-deps --wait --wait-timeout 180 web
```

The complete `scripts/update-deployment.ps1` updater also starts dependencies and
runs these migrations before replacing web containers. Keep the existing secrets
and volumes; do not reset the database or regenerate credentials.

`001_exercise_lifecycle` adds exercise availability and tracking type (`reps` or
`duration`). It recognises the original seeded Plank as timed without changing old
numeric prescriptions or logs. `002_exercise_library` inserts missing library entries.
The CLI serializes upgrades with an advisory transaction lock and commits SQL and
the `schema_migration` ledger together. Repeated upgrades are a no-op. Fresh-install
SQL has the same columns, catalogue and migration versions. There are now 11 tables
including the migration ledger. The Docker image includes the SQL files.

## Explicit administrator promotion

Replace `your_username` with an existing account's username:

```powershell
docker compose exec -T --index 1 web flask --app app promote-admin "your_username"
```

No account or password is created. Unknown usernames fail without changing accounts.
Registration always creates ordinary members, ignoring submitted privilege fields.
Every admin route checks the administrator flag loaded from PostgreSQL; revocation
takes effect on the next request.

Administrators use https://localhost:8443/admin/exercises to search, create, edit,
deactivate with confirmation, or reactivate exercises. Mutations use POST and CSRF.
The panel never deletes an exercise or rewrites saved workout snapshots.

## Onboarding and data preservation

Registration opens `/onboarding`. The dashboard prompts existing accounts missing
height, experience, goal or any recorded weight. Setup can be deferred. Forms reuse
the existing profile choices and decimal validation, and fill only missing fields.
Existing profile values take precedence on the server even with forged/stale forms.
Completed setup submissions leave data unchanged.

An older weight can be kept or today's weight added. If today already has an entry,
the user explicitly confirms using it; editing is available through the existing
bodyweight form. Duplicate races roll back the profile fill and show the existing
entry. Concurrent setup submissions are serialized. User IDs always come from the
authenticated session, and profile/measurement writes are transactional.

## Library and timed exercises

The seed contains 68 distinct exercises spanning calisthenics, free weights and
machines, with muscle group, equipment and concise instructions. It inserts missing
names only, preserving IDs, edits, custom entries and deactivation state. To repeat:

```powershell
docker compose exec -T --index 1 web flask --app app seed-exercises
```

Deactivated exercises are hidden from the public library and new prescription
pickers. Existing prescriptions can retain them, history remains readable, and
sessions can continue their already-assigned rep-based exercises. Exercise status
is rechecked under a shared row lock before writes, serializing against admin edits.

Plank, side plank, hollow hold, wall sit, dead hang and farmer carry are labelled
Timed. **Duration targets and duration-based set logs are outside this phase.**
They need duration fields and compatible logger/history validation. The server
rejects timed IDs in new rep-based prescriptions and sets. Legacy numeric targets
and logged reps remain as recorded, with explanatory notices; no seconds/reps
conversion is guessed or performed.

## UI and executed checks

Dark fitness branding and the green accent remain. Navigation has active states,
forms/buttons share spacing and focus styles, wide tables scroll inside their own
containers, and empty states offer useful next actions. The home page describes
implemented planning, logging and progress features. Diet and AI are not implemented.

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

Executed: **62 tests passed, zero skipped**, including the original suite, concurrent
onboarding, today's-weight confirmation, atomic duplicate handling, admin access and
revocation, lifecycle/snapshot preservation, timed restrictions, repeatable seeding,
upgrading the old schema and rollback of failed DDL. Migrations were applied to the
existing installation, then repeated successfully as a no-op. Existing records and
the original exercise IDs/content matched their pre-upgrade fingerprints.

An isolated browser context accepted the local certificate for UI validation. Home,
register and library pages had no horizontal page overflow at 375px and 1280px.
Real signup/onboarding/profile/bodyweight and non-admin rejection passed. A temporary,
explicitly promoted account verified the admin list/search/new/edit forms at 375px;
wide tables scrolled internally. The temporary account and its measurement were
removed. No existing account was automatically promoted.
