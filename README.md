# FitHub

A school project for workout planning, training history and nutrition planning.
Local AI produces drafts for human review. Saving a plan never logs a workout or meal.

[School guide and diagrams](docs/school-project.md) ·
[Six-minute demo and startup checklist](docs/presentation.md) ·
[Verification](docs/submission-verification.md) ·
[Backup/restore](docs/backup-and-restore.md)

## Prerequisites and first installation

Use Windows PowerShell, Git and Docker Desktop with its Linux engine running.
The isolated-install verifier requires Compose `!override`/`!reset` support
(tested with Compose 5.6.1). Docker supplies Python: no host Python is required for
setup, regression tests or the HTTP smoke. Chrome/Playwright are developer browser
test tools, not application dependencies. Allow storage/RAM for Docker images and,
separately, an optional local model.

Default free localhost ports: HTTP **8010**, HTTPS **8443**, logs **8088**,
PostgreSQL **5433**. Web replicas and Chroma have no published host ports.

```powershell
git clone https://github.com/jorogvas-dev/FitHub.git
Set-Location FitHub
git switch ui/purple-theme
New-Item -ItemType Directory -Force .secrets | Out-Null
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
docker compose exec -T web flask --app app import-recipes --dry-run
docker compose exec -T web flask --app app import-recipes --index
```

Setup generates `.env` only when missing and preserves existing secrets. Initialization
SQL runs only with an empty database volume; versioned migrations run before web
deployment. Recipe imports check pinned data/license hashes and update stable
source IDs and the FitHub-only Chroma index. Repeating imports is safe.

A fresh installation has **13 migrations, 23 tables, 68 exercises, 100 shared foods
and 100 active recipes**. An upgraded database may retain 32 inactive pilot recipes
for historical references.

Open **https://localhost:8443/** and accept the local self-signed certificate warning.
HTTP **http://localhost:8010/** redirects to HTTPS; logs: **http://localhost:8088/**;
readiness: **https://localhost:8443/health**. Use configured ports if changed.
Register your own account. **No default passwords or administrator accounts exist.**

## Update and migrations

```powershell
Set-Location E:\FitHub-School
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\update-deployment.ps1
docker compose exec -T web flask --app app import-recipes --index
```

Keep `.env`, `.secrets` and all existing volumes. Never remove the main database
volumes as an update step. For an application-only rebuild:

```powershell
docker compose build web
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose up -d --no-deps --wait --wait-timeout 180 web
```

The migration ledger/advisory lock makes migrations 001–013 repeatable. A second
`db-upgrade` reports that the database is up to date. Editing initialization SQL
does not upgrade a populated database. `docker compose stop` preserves data;
`docker compose up -d --wait --wait-timeout 300` starts the deployment again.

## Implemented features

- Private onboarding, profile, bodyweight and editable/skippable training preferences.
- Manual workout plans and validated AI drafts with explicit **Save as new plan**.
- Weighted/bodyweight sets, reps/RIR, captured prescriptions and completed history.
- Last-session references, progress chart/table, PRs and weekly recap.
- Persistent timestamp-based rest timer, optional sound, calendar and upcoming workouts.
- Manual diary, private foods/recipes, USDA Food Library and optional meal plan names.
- Recipe retrieval, exact daily upper limits, portion/snack fitting, confirmed meal
  swaps and separate diary logging.
- Protected exercise/food administration, responsive dark theme and keyboard focus.

## Configuration and AI

`.env.example` contains placeholders/defaults. Never commit `.env`, credentials,
backups, private documents, account data, database volumes or local model weights.

| Variable | Purpose |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database identity; preserve existing values |
| `SECRET_KEY` | Shared session, CSRF and signing secret across both replicas |
| `WEB_PORT`, `HTTPS_PORT`, `DOZZLE_PORT`, `POSTGRES_HOST_PORT` | Localhost ports |
| `WORKOUT_AI_ENDPOINT` | Default `http://host.docker.internal:3000/api/chat/completions` |
| `WORKOUT_AI_MODEL` | Default `qwen2.5-7b-nerd-uncensored-v1.0` |
| `NUTRITION_AI_MODEL` | Exact preset ID; default `fithub-coach` |
| `WORKOUT_AI_TOOL_IDS` | Optional comma-separated tool IDs; empty for fitness flows |
| `WORKOUT_AI_TIMEOUT_SECONDS` | Default 120, bounded to 1–180 seconds |

Compose supplies `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` and
`CHROMA_URL`. It fixes `WORKOUT_AI_API_KEY_FILE` to
`/run/secrets/fithub/openwebui-api-key.txt`. Put the API key in ignored
`.secrets/openwebui-api-key.txt`; both replicas mount it read-only. Keep credentials
server-side, outside URLs/forms/browser storage.

FitHub reuses separately running **Open WebUI** and its existing **LM Studio**
connection/model. FitHub does not start/download/switch that model. Enable API-key
access and authorize the selected model IDs. Verify IDs using FitHub credentials:

```powershell
docker compose exec -T web flask --app app nutrition-models
docker compose exec -T web flask --app app nutrition-smoke --kcal 2000 --protein 140 --carbs 230 --fat 65
```

The smoke uses synthetic targets/public recipes with no plan/diary writes.
`fithub-coach` is the exact preset ID, not its display name. Document tools and PDFs
are not needed. Missing services/credentials, invalid output or timeouts show clear
errors; manual flows remain usable. Workout Review has a server recap fallback.
See [Open WebUI integration](docs/openwebui-integration.md).

## Administrator access

Register normally, then explicitly promote an existing account:

```powershell
docker compose exec -T web flask --app app promote-admin your_registered_username
```

This creates no account/password. Exercise admin: `/admin/exercises`; food admin:
`/admin/foods`. Server role/ownership checks protect direct requests and mutations.

## Verification and backup

```powershell
# Real PostgreSQL in temporary schemas; inference mocked in regression tests.
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v

# Real HTTPS flows, temporary fictional account, no inference calls.
docker compose run --rm --no-deps -T --volume "${PWD}/scripts:/srv/scripts:ro" web python scripts/smoke_submission.py

# Separate project, ports and volumes; no AI key; automatic cleanup.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-fresh-install.ps1

# External backup, isolated restoration, matching public-table row counts.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup-postgres.ps1 -VerifyRestore
```

Fresh verification uses ports 18010/18443/18088 and refuses existing verification
resources. It never resets the main deployment. `check-deployment.ps1` also tests
replica failover and DNS recovery, temporarily replacing/stopping web replicas;
run it before, not during, a presentation. Live AI checks are reported separately.

## Troubleshooting and limits

- Docker unavailable: start Docker Desktop and wait for the Linux engine.
- Port occupied: change `.env`; do not stop unrelated services.
- Missing table/column: build, run `db-upgrade`, then recreate web.
- Recipe search unavailable: check Chroma health; repeat `import-recipes --index`.
- AI failure: run `nutrition-models`; check the key file, exact ID and Open WebUI's
  LM Studio connection. Never print credentials. Confirm the model is loaded;
  the inference deadline is intentional. Manual flows remain available.
- No allergy-safe meals: incomplete allergen metadata fails closed. More recipes
  do not automatically provide more verified allergy-safe options.
- Shortfalls: other hard limits, recipe ratios, portion bounds and bounded search
  may prevent filling targets. Targets are never silently changed.
- Silent timer: enable sound with a user gesture, again after reload if needed.
- Brief 502 after rebuilding web: wait for readiness/Docker DNS; inspect
  `docker compose ps` and `docker compose logs --tail 100 web nginx` if it persists.

This is a localhost school deployment, without password reset, email verification,
login rate limiting or public-hosting certification. Timed workout logging and
trainer PDF retrieval are not implemented. See the school guide for data licenses,
privacy boundaries and other limitations.
