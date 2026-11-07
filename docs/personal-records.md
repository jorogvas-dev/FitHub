# Personal Records

Implemented on the existing `ui/purple-theme` branch and verified locally on 2026-11-07. Earlier working-tree changes were preserved. Records extend the existing workout completion/history and [Training → Progress](./workout-progress.md) flow. No schema, migration, dependency or AI integration changes were needed.

## Completed-workout badges

Supported rep-based sets can receive a **New PR** badge in the completed workout view for:

- **Heaviest load:** a recorded kg load strictly above the exercise's earlier weighted maximum.
- **Reps at an exact load:** a rep count strictly above the earlier maximum at that same logged numeric load. Different loads are separate records.
- **Unweighted reps:** a rep count strictly above the exercise's earlier unweighted/bodyweight maximum. An unweighted set has `weight_kg = NULL`; recorded `0.00 kg` is a separate weighted load.

The first completed result in each category establishes a baseline and earns no badge. A newly encountered load establishes its own rep baseline, even if that set also improves an already-established heaviest-load record. Results at other loads or for other exercises never supply a rep baseline.

Comparisons include only the signed-in user's earlier completed sessions. Ordering is `(finished_at, session_id)`, so sessions completed on the same day, or even at the same timestamp, have deterministic order. Active sessions, the viewed session itself and later completions are excluded. Historical earned badges therefore remain valid when later records improve.

All sets in a session are compared to the same earlier-session baselines. Only one winning set earns a badge for each record: highest weight, then higher reps for the load record; highest reps for each exact-load/unweighted rep record; then lower set number and set ID for an exact tie. Several different load-specific rep records may improve in the same workout. Equal results never count as improvements.

The badge includes its record type as visible text. Completed workouts remain read-only; active workouts do not display PR badges. No PR flags are saved to the database. The [record service](../app/services/personal_records.py) reads prior maxima for all relevant exercises/loads in one query, then determines the current session's winning sets.

## Selected-exercise Progress summaries

The selected exercise's Progress page now includes:

- The heaviest recorded set, its reps, completion date and an owned-session link.
- An accessible **Rep records by logged load** table, with the best reps, first-achieved date and session for every recorded kg load.
- A separate best **Unweighted bodyweight** rep count and date when available.

Summary records include baselines as well as later improvements. A tied heaviest load prefers the set with higher reps. Exact weight/rep ties retain the earliest completion timestamp, session ID, set number and set ID. The summary query considers every completed logged set, including lighter sets omitted from the chart's per-session heaviest-set selection.

The existing chart and accessible completed-session history table remain. Their one-point-per-completed-session selection is unchanged. Recorded weights use the shared [workout weight macro](../app/templates/workouts/_values.html), retaining numeric precision and the existing Bodyweight label. No weights are rounded into another load category, and bodyweight is never converted into zero kg or inferred from measurements.

## Ownership and older history

Records match actual stable `set_log.exercise_id` values across plans. All record queries explicitly filter the authenticated owner. Direct exercise requests outside that user's completed supported history retain the existing 404 behavior; request-supplied owner IDs cannot change the scope.

Deleted plans do not remove log-derived records or badges. Existing session snapshots continue to preserve plan/day labels. Older ordinary notes with valid logged IDs remain usable. Snapshot prescriptions without usable exercise IDs do not receive a name-based match or guessed badge; explicitly identified logged sets remain separate, following the existing Progress behavior.

Only the app's supported rep-based weighted/unweighted sets participate. Inactive exercises with rep history remain supported; legacy timed values are still readable in history but do not become rep PRs. Explicit zero loads and zero rep values retain their existing logged meaning.

Weekly recaps and AI suggestions remain outside this milestone.

## Executed verification

**211 Docker/PostgreSQL regression tests passed**, zero skipped, in **69.144 seconds**. The 12 new focused PR tests also passed separately. Coverage includes:

- First-session and independently established weighted/load/bodyweight baselines.
- Strict heaviest-load and exact-load rep improvements; new/nearby loads and equal results.
- Multiple qualifying sets, deterministic exact ties, same-day/same-timestamp sessions and earlier-completion ordering.
- Bodyweight PRs, explicit zero load/zero reps, and category separation.
- Historical badges unaffected by later completions or active sessions.
- Summaries from all loads, including non-chart sets, with earliest achievement dates on exact ties.
- Actual finish routing, deleted plans, ordinary notes, missing snapshot IDs and unsupported legacy timed sets.
- Account isolation, direct requests, bulk prior-record reading and unchanged sessions/sets/migration records on reads.

**39 browser checks passed** through the deployed HTTPS app at **1280 px and 375 px**, using synthetic accounts and session logs. The completed example showed exactly five intended winning badges, including one winner among duplicate best sets. A later 80 kg result did not remove the viewed workout's historical 55 kg badge. Progress retained three session points/history rows, showed per-load records from lighter sets, selected the earliest tied bodyweight result, and kept recorded zero separate from unweighted reps. Keyboard focus and table/page widths passed; all table columns fit at both widths. The temporary accounts and records were removed.

Both rebuilt web replicas are healthy. Dependency/schema files and the existing eight migration versions are unchanged for this milestone. Reads do not persist PR data or modify completed logs.

## Exact PowerShell rebuild command

With the existing database and other FitHub services running:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose up -d --no-deps --wait --wait-timeout 180 web
```

No database upgrade is required. Run the full regression suite in isolated PostgreSQL schemas with:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

## Screenshots

All displayed account and workout data is synthetic.

| View | 1280 px | 375 px |
| --- | --- | --- |
| Completed workout and winning badges | [Desktop](./screenshots/personal-records/completed-workout-1280.png) | [Mobile](./screenshots/personal-records/completed-workout-375.png) |
| Heaviest set, load-specific rep records and retained chart/history | [Desktop](./screenshots/personal-records/weighted-progress-1280.png) | [Mobile](./screenshots/personal-records/weighted-progress-375.png) |
| Separate unweighted record | [Desktop](./screenshots/personal-records/bodyweight-progress-1280.png) | [Mobile](./screenshots/personal-records/bodyweight-progress-375.png) |
