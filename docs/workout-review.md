# AI Workout Review

Implemented on `ui/purple-theme` using the existing completed-workout page,
Personal Records logic and Open WebUI adapter. No migrations, persisted reviews,
new dependencies, plan writes or log writes were added.

## Request and privacy

- Completed workouts expose a **Review with AI** button. Only a signed-in,
  CSRF-protected `POST /workouts/<session_id>/review` invokes inference. Initial
  GETs and subsequent visits do not invoke or persist a review. Another member's
  workout returns 404; active workouts reject review with 409.
- The selected workout's actual logged sets, its saved prescription snapshot when
  available, previous comparable completed sets and earned PR categories are sent.
  Ordinary notes and the current editable plan are not substituted for a missing
  saved snapshot. Deleted plans retain snapshot/log review support.
- Earlier comparable sets are fetched in one bulk query, matched by stable
  exercise ID and split into weighted and unweighted categories. Selection uses
  `(finished_at, session_id)` strictly before the selected session. Active, later,
  current and other-account sessions are excluded. There is no name matching.
- Connections are released before contacting inference. Reviews remain ephemeral
  and advisory. Opening or requesting one changes no plans, workouts or sets.

## Server facts and model contract

- The server computes logged set/exercise counts, weighted and bodyweight best
  sets, exact-load best-rep changes, heaviest-load changes and earned PR facts.
  An explicit 0 kg load remains weighted and is distinct from unweighted sets.
- Eligible targets require a single valid saved rep-based prescription, an active
  exercise, its full prescribed set count, one common logged load, all reps within
  the saved range, room to add a rep within that range, and earlier same-load
  history. The minimum target is the lowest logged set's reps plus one; the maximum
  and set count come from the snapshot. Load stays unchanged. No kg increment,
  timed prescription, missing history, ambiguous prescription or mixed-load target
  is invented. These are optional practice targets, not automatic progression.
- Structured output contains only known highlight IDs and up to three unique
  eligible target IDs with approved explanation IDs. The model chooses the
  observations and priority and selects an applicable explanatory phrase. The
  displayed factual sentences, exercise references, numbers and target wording
  all resolve from server-owned data. Unchecked free prose is never rendered.
- Extra fields, arbitrary numbers/text, unknown IDs, duplicate highlights/targets,
  unsupported explanations, excess suggestions and malformed/oversized responses
  are rejected. Invalid output, authentication/provider failure, busy inference,
  timeout or oversized input yields a short server-generated recap, clearly
  labelled **AI review is unavailable**. No unsupported target is shown in fallback.

## Existing inference configuration

The shared adapter reuses `WORKOUT_AI_ENDPOINT`, `WORKOUT_AI_MODEL`,
`WORKOUT_AI_API_KEY_FILE` and `WORKOUT_AI_TIMEOUT_SECONDS`, including its bounded
executor/concurrency limit and authenticated response transport. Workout draft
generation continues using the same adapter and validation/correction behavior.
Reviews explicitly disable document tools because their facts are already supplied.

Verified running values (credential value remains **[REDACTED]**):

- Endpoint: `http://host.docker.internal:3000/api/chat/completions`
- Existing model: `qwen2.5-7b-nerd-uncensored-v1.0`
- Existing LM Studio connection is reused through Open WebUI.
- Deadline: 120 seconds, configurable within the existing 1–180 second limit.
- nginx gives the exact review route 195 seconds to let FitHub return its own
  bounded failure recap. Other ordinary routes retain the 30-second read timeout.
- The submitting button disables while waiting, announces a status, and blocks
  repeat submissions in that page. Browser back navigation resets the controls.

## Verification

### Mocked regression tests

The full Docker/PostgreSQL suite passed **230 tests, zero skipped**, in **78.577
seconds**, including eleven new review tests. All inference in that suite is
mocked; it does not call Open WebUI.
The focused eleven review tests passed before the full run. Coverage includes:

- authentication, CSRF, ownership, completed-only review and zero GET inference;
- earlier-session selection including same-completion-time session-ID ordering,
  exclusion of active/future/other-account data, and one bulk history read;
- weighted, explicit-zero and unweighted sets; exact-load rep deltas and PR facts;
- eligible targets, missing history/snapshots/IDs, mixed loads, inactive and timed
  exercises, out-of-range sets and the top of a prescribed rep range;
- fabricated values/text/references/targets, duplicate/extra fields and malformed
  or oversized output, provider/authentication failures, timeouts and busy inference;
- unchanged sessions, sets, plans and measurements, deleted-plan snapshots, and
  protection against using a subsequently edited plan's prescription;
- adapter endpoint/model/authentication/deadline reuse and disabled document tools.

The existing historical read-only presentation test now allows only the explicit
CSRF-protected review form, while continuing to forbid set edits and workout writes.

### Live Open WebUI check — separate from mocked tests

A temporary synthetic account, two completed sessions and eight logged sets were
used through the rebuilt HTTPS UI. One explicit button click produced one real
review POST. Existing Open WebUI returned HTTP 200 in **11.350 seconds**; FitHub
validated the result and displayed **AI-prioritized review** with **two eligible
suggestions**, one weighted and one unweighted. No inference mock was used for
that request. Session, set and plan counts stayed unchanged.

**24 browser checks passed** at **1280px and 375px**: initial empty review, explicit
generation, live validation, suggestion limit, weighted values, no document/card
overflow, visible keyboard focus, fallback labelling, no unsupported fallback
targets, read-only data and no inference on GET revisits. Fallback screenshots use
server-rendered HTML with an explicitly mocked provider outage; they are not
evidence of a live outage. Both web replicas and nginx are healthy; `nginx -t`
passed. The temporary account and its logs were removed; the persistent demo was
preserved. Reviewed the live and fallback card screenshots at both widths.

| View | Desktop | Mobile |
| --- | --- | --- |
| Live validated AI review | [1280px](screenshots/workout-review/review-live-card-1280.png) | [375px](screenshots/workout-review/review-live-card-375.png) |
| Mocked provider-failure recap | [1280px](screenshots/workout-review/review-fallback-card-1280.png) | [375px](screenshots/workout-review/review-fallback-card-375.png) |

## Exact PowerShell commands

Rebuild both web replicas and the nginx route timeout configuration:

```powershell
Set-Location E:\FitHub-School
docker compose build web nginx
docker compose up -d --no-deps --wait --wait-timeout 180 web nginx
docker compose exec -T nginx nginx -t
```

Run the complete suite in disposable PostgreSQL schemas:

```powershell
Set-Location E:\FitHub-School
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

Browser checklist: open a completed workout, select Review with AI, inspect the
advisory facts/targets, verify no plan or log changes, then check an active workout
has no review action. Test another account's URL directly; it must return 404.
