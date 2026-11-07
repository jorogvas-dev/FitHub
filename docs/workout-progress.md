# Workout Progress milestone

Implemented on the existing `ui/purple-theme` branch and verified locally on 2026-11-07. Earlier working-tree changes were preserved. This milestone added last-session references and exercise progress. [Personal Records](./personal-records.md) are covered by the subsequent milestone; weekly recaps and AI suggestions remain later work.

## Last session reference

Active workouts show **Last completed session** under each identified exercise, including exercises logged outside the original plan. References match the signed-in user's actual `set_log.exercise_id` across all their plans. Active sessions and completed sessions without logged sets for that exercise are excluded.

The newest match is selected by `finished_at DESC, session_id DESC`. Its full set list is shown in `set_number, set_id` order, with recorded weight and reps and a link to the completed session. A missing match displays **No previous sets**.

The [progress service](../app/services/workout_progress.py) fetches all exercise references in **one bulk query**, using `DISTINCT ON` and a second join to the selected sessions' sets. It does not make one query per exercise. Completed workout pages keep their original read-only presentation and do not show changing references.

## Exercise progress

**Progress** is linked under Training in the shared navigation and training tabs. The page is available at `/workouts/progress`, with an optional `exercise_id` query parameter. The picker contains only rep-based exercises that the signed-in user actually logged in completed workouts, including inactive catalogue exercises with historical sets.

Each completed session contributes exactly one set:

1. Highest recorded weight, using exact database numeric values; bodyweight (`NULL`) sorts after recorded loads.
2. Higher reps when the weight is equal.
3. Lower set number, then set ID, for an otherwise exact tie.

Points are ordered by completion timestamp and session ID. Sessions on the same day remain separate; timestamps and session links in the table distinguish them. Chart positions are spaced evenly by session order, not by elapsed calendar time. Empty history shows an explanation, and a single session shows one point without implying a trend.

The chart is server-rendered SVG with an accessible title/description and a complete captioned table of completion dates, weights and reps. Chart and table appear alongside each other on desktop and stack on mobile. All three table columns fit at 375 px. The picker, button and data region support visible keyboard focus.

The shared [weight macro](../app/templates/workouts/_values.html) preserves the logger's existing Decimal display and **Bodyweight** label. Bodyweight is never converted to zero kilograms or inferred from body measurements. When a session contains only bodyweight sets, its highest-rep set is selected. Bodyweight-selected points use a separate reps scale; weighted points use kg. Mixed history still contains one point per completed session. Timed durations are not supported by the existing logger; legacy timed values remain visible in workout history and last references, labelled as recorded legacy reps, and are excluded from the rep-based Progress picker.

## Historical data and ownership

The existing v1 session snapshot parser is shared with workout detail/history. Snapshot day and plan labels survive deletion of the original plan. Ordinary older notes fall back to available owned day/plan labels or **Original training day unavailable**; valid logged exercise IDs still supply progress.

Older snapshot prescriptions without usable exercise IDs remain visible but cannot receive a name-based match or an exercise-specific Add set link. Explicitly identified logged sets remain separate from an unidentified prescription, even when their displayed names are equal. Planned targets or snapshot-only exercises never become logged progress points. The deployed schema requires non-null IDs in actual set logs; this change introduces no guessed IDs or data rewrite.

Every history query is scoped to the authenticated user. Direct requests for IDs outside that user's completed, supported history return 404; malformed IDs return 400. Request-supplied owner IDs do not affect selection. Workout detail and reference links retain the existing ownership checks.

**No schema, migration or dependency changes were made for this milestone.** The existing eight migration versions remain in place. Both views are read-only and do not create sessions, modify sets or finish workouts. There is no chart library, browser inference call or added API service.

## Executed verification

**199 Docker/PostgreSQL tests passed**, zero skipped, in **64.577 seconds**. The full suite includes **10 new focused progress regression tests** covering:

- Latest completion and deterministic session ties; later empty completions and active sessions excluded.
- A single bulk reference query across planned and additional logged exercises, with all sets ordered correctly.
- Cross-plan matching, catalogue renames/inactive exercises and deleted-plan snapshot preservation.
- Highest weight, higher-rep ties, exact ties, and separate sessions on the same day.
- Bodyweight versus explicit zero, zero reps, empty/single histories and stable chart positions.
- Legacy notes, missing snapshot IDs and snapshot-only targets without actual logs.
- Picker/direct-request ownership, unsupported timed history, malformed IDs and read-only session/set/ledger preservation.

**49 browser checks passed** through the deployed HTTPS app at **1280 px and 375 px**, with ten screenshots. Synthetic accounts exercised empty and single histories, active-workout references, no-match and missing-ID cases, three completed sessions including two on the same day, a weight tie resolved by reps, deleted-plan labels/links, direct unauthorized IDs, mixed bodyweight/zero history, keyboard focus and responsive chart/table layouts. All table columns fit without sideways scrolling. Sessions, sets and the migration ledger matched before/after the reads. The temporary accounts and their records were removed.

Both rebuilt web replicas are healthy. Existing logging, read-only completion, nutrition and draft-generation regressions also passed.

## Exact PowerShell rebuild command

With the existing FitHub database and other services running:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose up -d --no-deps --wait --wait-timeout 180 web
```

No database upgrade is required for this milestone. To rerun the full suite in isolated PostgreSQL schemas:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

## Screenshots

All displayed account and workout data is synthetic.

| View | 1280 px | 375 px |
| --- | --- | --- |
| Active workout references | [Desktop](./screenshots/workout-progress/active-workout-1280.png) | [Mobile](./screenshots/workout-progress/active-workout-375.png) |
| Exercise progress / same-day sessions | [Desktop](./screenshots/workout-progress/exercise-progress-1280.png) | [Mobile](./screenshots/workout-progress/exercise-progress-375.png) |
| Bodyweight and recorded zero | [Desktop](./screenshots/workout-progress/bodyweight-progress-1280.png) | [Mobile](./screenshots/workout-progress/bodyweight-progress-375.png) |
| Single session | [Desktop](./screenshots/workout-progress/single-session-1280.png) | [Mobile](./screenshots/workout-progress/single-session-375.png) |
| Empty history | [Desktop](./screenshots/workout-progress/empty-history-1280.png) | [Mobile](./screenshots/workout-progress/empty-history-375.png) |
