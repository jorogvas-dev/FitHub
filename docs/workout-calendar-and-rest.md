# Workout calendar and rest timer

Implemented on `ui/purple-theme`, using Flask/Jinja, the existing PostgreSQL
connection/ownership/CSRF helpers, workout snapshots, date handling and theme.
No dependencies or inference calls were added. Existing AI Workout Review and
nutrition work remain intact.

## Rest behavior

Plan exercise forms now accept optional **1–3,600 seconds** of rest. New sessions
capture that prescription in their existing snapshot; later plan edits cannot
change an active workout's rest duration. Old snapshots remain valid and offer a
manual timer with a **90-second default**. Repeated exercise prescriptions use the
logged set number within their consecutive target-set ranges; ambiguous extra
sets fall back to manual rest rather than guessing.

Only a committed new set produces a signed, owner/session-bound timer event.
Invalid submissions, duplicate set numbers, edits and deletions produce no new
event. The browser consumes a successful event once, removes it from the URL and
ignores older events. A new prescribed or manual rest replaces the current timer.
The panel is available on both workout detail and the set form, so logging and
validation remain usable while it runs.

The timer stores an absolute deadline, paused remaining milliseconds and original
duration in localStorage, keyed by signed-in user and workout. It calculates time
from timestamps rather than counting interval ticks. Reloads preserve the deadline;
visibility/focus restores the display immediately and verifies session status.
Start/pause/resume, reset-to-prescription (paused), skip and ±15 seconds are
supported. A separate **Start manual rest** action replaces any current timer.
Adjustments are bounded at zero and one hour. Reset retains the original duration.

Completion has an explicit visual/status message. Sound defaults off and must be
enabled by a user gesture; blocked or unsupported audio produces a visible note
while keeping the timer functional. Audio may require a new gesture after reload.
The numerical display has `role="timer"` and `aria-live="off"`; state changes and
completion use a separate status region, without second-by-second announcements.
Keyboard focus uses the app's existing visible focus styles.

Completion removes stored timer state and clears other open tabs. Logout/account
switch clears rest storage. If storage is unavailable, timing works for the page
and a note explains that reload persistence is unavailable. This is a browser-local
timer; it does not synchronize rest across devices or change workout data.

## Calendar and start decisions

**Training → Calendar** provides a Monday-first month table and a chronological
agenda, previous/next month and Today links. Scheduled dates are PostgreSQL
`date` values, accepted in the existing 1900–2100 date range. Multiple entries may
share a date. All mutations are authenticated POSTs with CSRF protection.

The calendar uses the saved nutrition/profile timezone setting and the existing
**Europe/Sofia** fallback. Completed workouts use `finished_at`, converted to that
timezone. Month boundaries are calculated from separate local midnights, including
DST changes. Completed sessions are shown once on their actual completion date;
a linked entry also states its original scheduled date. A completion may therefore
appear in a different month from its original scheduled date.

Scheduling creates no workout session. The entry review page offers **Start
workout**, date rescheduling and explicit cancellation. Past uncompleted entries
remain **Not completed**; they are never silently moved. Cancelled entries remain
visible as cancelled. The dashboard shows the next **three** non-cancelled,
uncompleted entries from today onward, scoped to the signed-in user.

Inspection found that the original manual start route always inserted a session.
Manual and calendar starts now share snapshot/session creation and serialize on
the owner row. **An existing active session for the same plan day is resumed**.
The pre-existing ability to have active workouts for different plan days remains.
Existing legacy sessions are retained; none are automatically completed or removed.

Calendar entries have a unique session link. Starting the same entry repeatedly
returns that session, including after completion. Two entries cannot claim one
session: the later start opens the already linked active workout with an explanation
and leaves the second entry unfulfilled. After the first session is finished,
starting the second entry creates its own session. Completion is derived from the
linked session's completion timestamp, so there is no second fulfillment write or
counter to duplicate.

Create forms use signed request nonces and a per-user database uniqueness constraint:
repeat submissions with identical values return the original entry; changed reuse
returns 409. Reschedule previews include the entry version and current prescription
fingerprint; stale/repeated reschedules return 409 without another change.
Repeated cancellation returns the same cancelled entry. Started entries cannot be
rescheduled or cancelled; the linked workout remains authoritative.

Each entry retains its original prescription snapshot. A plan/day deletion sets
only the source day reference to null; original names and session history survive.
Deleted days cannot start a new workout and show clear cancel/replacement guidance.
Edited prescriptions are displayed before starting and require explicit acceptance;
edits after preview cause a 409/review rather than silently using changed targets.
An already active session keeps its own captured prescription. Plan locks precede
schedule locks so deletion and starts do not deadlock.

Calendar data is fetched in bulk: two month queries for scheduled entries and
completed sessions, and one bounded dashboard upcoming query. Ownership is checked
again on every entry operation and direct workout/timer-state request.

## Migration and verification

Additive migration **012_workout_calendar** adds optional `rest_seconds` and the
`workout_schedule` table, its stable identity, request nonce, version, date index,
snapshot and unique session link. The fresh-install SQL has the same changes.
The migration applied to the existing installation; a second CLI run reported
“Database is up to date.” Existing sets, plans and nutrition data are retained.

The focused PostgreSQL suite covers scheduling, multiple entries per date,
rescheduling, cancellation, invalid dates, ownership and CSRF; timezone month
boundaries; changed/deleted plans; repeated starts and finish; concurrent calendar
starts, concurrent manual/calendar starts and deletion racing with start;
migration from the previous schema; snapshot rest and success-only timer events.

**15 focused tests passed** in **8.033 seconds**. The final full Docker/Python 3.12/
PostgreSQL suite passed **286 tests, zero skipped, in 100.729 seconds**. Both web
replicas and PostgreSQL/Chroma report healthy after the final rebuild. Browser
checks use a temporary synthetic account and normal
HTTPS forms, with labelled sample plan/day names. QA accounts were removed after
capture; the persistent demo account was not modified.

**44 Chrome browser checks passed** at **1280 px and 375 px**. These include:

- Calendar month controls, table/agenda, multiple entries, start, completion shown
  once, reschedule and cancellation, dashboard upcoming entries, and no horizontal
  page overflow at either width.
- Prescribed rest after successful insert, 90-second manual default, reload and
  paused reload, real tab switching, and no replacement after failed duplicate or
  browser-invalid submissions.
- Replacing rest after another successful set, pause/resume, reset, skip, ±15 seconds,
  manual replacement, visual completion and visible keyboard focus at both widths.
- **Simulated** 45-second delayed-tick catch-up and completion using Playwright's
  browser clock. This verifies timestamp calculation; it is not a claim of a
  45-second real suspended-process test. The actual tab-switch check is separate.
- **Simulated blocked Web Audio** with a caught browser restriction, completion
  clearing persisted state and another tab, logout cleanup, and zero JavaScript
  page errors. No physical-speaker audibility test is claimed.

One earlier presentation test assumed restarting an unfinished day created a fresh
session. It now explicitly finishes that session first, then verifies updated
prescriptions in the next session, consistent with the new resume rule.

## Screenshots

All visible training data in these captures is labelled synthetic sample data.

| View | 1280 px | 375 px |
|---|---|---|
| Calendar and agenda | [Desktop](screenshots/workout-calendar/calendar-1280.png) | [Mobile](screenshots/workout-calendar/calendar-375.png) |
| Active workout and timer | [Desktop](screenshots/workout-calendar/active-timer-1280.png) | [Mobile](screenshots/workout-calendar/active-timer-375.png) |
| Dashboard upcoming workouts | [Desktop](screenshots/workout-calendar/dashboard-upcoming-1280.png) | [Mobile](screenshots/workout-calendar/dashboard-upcoming-375.png) |

## PowerShell deployment

Run from the repository. Apply the additive migration **before** starting the new
web image. No volume reset, new dependency or nginx configuration change is needed.

```powershell
Set-Location E:\FitHub-School
docker compose up -d --wait --wait-timeout 120 db chroma
docker compose build web
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose up -d --no-deps --wait --wait-timeout 180 web
docker compose up -d --no-deps --wait --wait-timeout 120 nginx

# Repeatable upgrade check: reports that the database is up to date.
docker compose run --rm --no-deps -T web flask --app app db-upgrade

# Full regression suite, using isolated PostgreSQL schemas.
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```
