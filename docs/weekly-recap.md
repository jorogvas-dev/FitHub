# Weekly recap

Implemented on `ui/purple-theme` using the existing dashboard cards, purple
palette, weight precision, completed workout history and Personal Records rules.
No schema migration or dependency changes are required.

## Behavior

- The dashboard defaults to **This week**. Its **Last week** link selects the
  preceding week; the selection survives reloads. The card prints both exact dates
  and the saved timezone. An absent settings row uses the existing Europe/Sofia
  fallback, shared with the dashboard's nutrition date.
- A week begins at local Monday midnight and ends just before the following local
  Monday. Each boundary is converted independently to UTC, including DST changes.
  Completed workouts are counted by completion time, including workouts with no
  sets. Active workouts do not contribute.
- **New records** counts each earned record category. Record awarding is shared
  with the completed workout view: first results establish baselines, strict
  improvements earn records, and only the best set per category in a session wins.
  Exact loads and unweighted bodyweight remain separate. History before the week
  supplies the baseline, and selected-week sessions advance it in completion-time
  and session-ID order. A session never supplies its own baseline.
- Bodyweight change uses the earliest and latest dated measurements within the
  selected week, retaining exact decimal precision and displaying both dates. One
  measurement displays its value and explains that two are needed. No measurements
  displays an Add weight link. Zero change remains a valid change.
- Recap data takes at most three bulk queries: selected sessions and sets, grouped
  earlier PR baselines, and selected-week measurements. All filter on the signed-in
  user's ID. Deleted plans and old ordinary notes do not remove logged history;
  snapshot names are never used to guess exercise identity. Unsupported timed sets
  do not contribute records.
- Dashboard GETs remain read-only. Existing nutrition cards, profile/weight history,
  manual workout workflows, and stored data are preserved.

## Verification

- Docker/PostgreSQL suite: **219 passed, zero skipped** (73.148 seconds on the
  first full run), including eight new weekly recap tests and all existing
  Personal Records, Progress, dashboard, workout and nutrition tests.
- New tests cover Sofia and saved Los Angeles Sunday/Monday boundaries, completion
  versus start times, exclusive next-Monday bounds, 167/169-hour DST weeks, baselines
  before the selected week, same-instant session ordering, within-session winners,
  strict ties, bodyweight records, timed-set exclusion, completed workouts without
  sets, zero/one/multiple weigh-ins, precise negative/zero/positive changes, forged
  account parameters, anonymous access and read-only bulk fetching.
- The multiple-categories-on-one-set aggregation test supplies two reasons through
  the shared PR helper's contract and verifies that both are counted. This is a
  synthetic aggregation case; the real PR rules retain their existing first-result
  baseline behavior at every exact load. Real completed-workout badge counts are
  also compared directly with recap counts.
- Live Chrome browser: **32 checks passed** over the rebuilt HTTPS app at **1280px
  and 375px**. Verified default/switch/reload behavior, exact ranges, completed and
  record counts, empty/single/multiple weight states, 44px switch touch targets,
  visible keyboard focus, and no document or card horizontal overflow. Reviewed
  screenshots at both widths. Both rebuilt web replicas became healthy.
- Browser checks used a dedicated temporary synthetic account and removed it and
  its fixtures afterward. The existing persistent sample account was preserved;
  its dashboard currently demonstrates one completed workout, six new records,
  and a single weekly weigh-in. No inference calls were needed.

## Screenshots

| Selected week | Dashboard 1280px | Dashboard 375px | Card detail |
| --- | --- | --- | --- |
| This week | [Desktop](screenshots/weekly-recap/this-week-1280.png) | [Mobile](screenshots/weekly-recap/this-week-375.png) | [Mobile card](screenshots/weekly-recap/this-week-card-375.png) |
| Last week | [Desktop](screenshots/weekly-recap/last-week-1280.png) | [Mobile](screenshots/weekly-recap/last-week-375.png) | [Mobile card](screenshots/weekly-recap/last-week-card-375.png) |

## PowerShell commands

Rebuild and replace both web replicas while retaining the existing database:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose up -d --no-deps --wait --wait-timeout 180 web
```

Run the full suite in isolated PostgreSQL schemas:

```powershell
Set-Location E:\FitHub-School
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

Open the [dashboard](https://localhost:8443/dashboard), switch between This week
and Last week, then follow Exercise progress or Weight history to inspect the
underlying owned logs.
