# Local AI workout drafts

FitHub now offers an optional training-preferences step after the existing onboarding. It reuses the account's height, goal, experience and latest dated bodyweight without changing them. The new fields are days per week (1–7), minutes per session (15–180), available equipment and structured movement exclusions. Bodyweight-only training needs no equipment selection. **Skip for now** works with blank survey fields and keeps the manual builder available. Preferences remain editable from Fitness profile and Workout plans.

After saving preferences, **Generate workout plan** opens a reviewable generation form. It is also available from Workout plans. A complete base profile and bodyweight record are required for generation; neither is invented or overwritten.

## Existing AI integration

FitHub now calls the running **Open WebUI 0.12.5**, reusing its existing LM Studio connection and loaded model:

- Docker endpoint: `http://host.docker.internal:3000/api/chat/completions`
- Configured model: `qwen2.5-7b-nerd-uncensored-v1.0`
- Native `response_format` / `json_schema` structured output.

The adapter authenticates with a backend-only API key read from the ignored, protected `.secrets/openwebui-api-key.txt`, mounted read-only into both replicas. The key is excluded from the Docker build context and Git. No key is sent to the browser or included in model messages. Open WebUI retains its configured upstream `http://host.docker.internal:1234/v1`; FitHub does not call that connection directly or change the model setup.

The existing workspace planner validates legacy task/document workflows, not FitHub workout documents. Workout requests therefore explicitly disable tools. A separate live read-only planner-tool test confirmed actual execution through Open WebUI's returned tool result. See the [Open WebUI verification report](./openwebui-integration.md) for the actual tool IDs, contracts and evidence.

Both web replicas receive the same configuration through Compose:

| Setting | Default |
| --- | --- |
| `WORKOUT_AI_ENDPOINT` | `http://host.docker.internal:3000/api/chat/completions` |
| `WORKOUT_AI_MODEL` | `qwen2.5-7b-nerd-uncensored-v1.0` |
| `WORKOUT_AI_TOOL_IDS` | Empty; comma-separated backend tool IDs if compatible tools are configured later |
| `WORKOUT_AI_API_KEY_FILE` | `/run/secrets/fithub/openwebui-api-key.txt` |
| `WORKOUT_AI_TIMEOUT_SECONDS` | `120` (accepted range 1–180) |

An existing operator override can be set in the local environment configuration. The endpoint must be an HTTP(S) Open WebUI URL with path `/api/chat/completions` or `/api/v1/chat/completions`, without embedded credentials, query or fragment. Browser input and model output cannot choose an endpoint, model or tool ID. The adapter does not follow redirects or use proxy environment settings. Missing credentials, rejected credentials, unavailable inference and timeouts produce clear errors while preserving the manual builder.

The web request waits at most the configured inference budget. The proxy allows 195 seconds specifically for generation, above the maximum 180-second inference budget; other routes keep a 30-second read timeout. Gunicorn has a 210-second worker timeout. Two bounded inference workers per application process prevent unbounded background work when a server stalls. Provider transport operations also have timeouts, response size is bounded, and errors never create a plan. The button shows a waiting state and prevents repeat submissions while generation runs. AI availability is deliberately independent of the ordinary app health check, so an inference outage does not disable manual workflows.

## Catalogue and response validation

The model receives only the training survey, current base-profile values and up to **48** eligible catalogue entries with IDs, names, muscle groups and equipment. Names, email addresses, diary data, PDFs and trainer-document content are not included.

Equipment is mapped conservatively from known catalogue labels to selected equipment. Composite requirements must all be available; bench pressing also requires the appropriate bench. Unknown equipment descriptions are excluded. Movement exclusions use the labelled, server-owned pattern groups shown in the survey. Unclassified custom movements are excluded when exclusions are active, rather than assuming that they comply. These catalogue filters are not a medical assessment.

Output must be a strict JSON object containing a plan name and exactly the requested number of training days. Each day contains a name, estimated minutes and 1–8 exercise prescriptions. Each prescription contains only `exercise_id`, `sets`, `reps_min` and `reps_max`. Validation rejects:

- Unknown IDs or IDs outside the supplied candidate list; inactive or timed exercises.
- Unsupported equipment or excluded movements, including catalogue changes after generation.
- Extra fields such as seconds/durations, missing fields, duplicate JSON keys, non-finite numbers, prose, oversized responses and invalid structures.
- Boolean/fractional prescriptions, sets outside 1–6, reps outside 1–30, inverted ranges and duplicate exercises within a day.
- A different day count, duplicate day names or a session exceeding the selected estimated budget.

The generation prompt requires distinct exercise IDs within each day; IDs may be reused on different days. The response schema caps rows by the available eligible IDs, up to eight. If an otherwise bounded model response fails validation, the adapter asks the same model for one complete correction using the validation feedback. Duplicate feedback includes the affected day and ID. Both calls share the original timeout, and authentication/transport failures are not retried. The server does not silently deduplicate or merge prescriptions. A corrected draft must pass all checks before private storage, and duplicates remain invalid during preview updates and saves.

Session duration is explicitly an **estimate**, recalculated as `ceil(5 + 2.5 × total sets)` minutes. It includes a five-minute warm-up allowance. It is not a guarantee of actual session duration. Estimates are recalculated after edits rather than trusting a model's duration claim.

## Private preview and explicit save

Generated documents are stored in the private `workout_draft` table, with the generating preferences, candidate IDs and a revision. They remain drafts through generation, GET preview and **Update preview**. Workout plans shows up to 20 recent unsaved drafts for the current account.

The preview reuses existing plan-editor conventions: names, catalogue exercise selection, sets and rep-range fields. An exercise can be removed, replaced or added; an additional optional row appears after each update, up to eight exercises per day. Change the requested day count in preferences and generate another draft.

Only **Save as new plan**, with the confirmation checkbox, can insert a plan. Save revalidates the edited document against current preferences and current catalogue records. Row locks serialize duplicate confirmations and prevent catalogue/preferences changes during validation and insertion. A revision rejects stale updates from another tab. Plan, days and prescriptions are inserted atomically, then the existing plan editor opens. A repeated save returns the already-created plan; deleting that plan does not make the draft saveable again. No workout session, set log or coach message is generated.

Migration `007_workout_drafts` adds only `training_preferences` and `workout_draft`, plus their indexes and constraints. It does not rewrite fitness-profile, bodyweight, plan, nutrition or workout records. Fresh installations use the equivalent seventh init script. The migration is repeatable through the existing versioned upgrade command.

## Executed checks

- **179 Docker/PostgreSQL regression tests passed**, zero skipped. Inference in the regression suite is mocked, including authentication, malformed envelopes, returned tool-execution evidence, provider HTTP responses and timeouts. Coverage includes skipped onboarding, preserved profiles/bodyweight, survey validation, eligible-catalogue bounds, strict model-output rejection, one bounded correction with a shared deadline, duplicate exercises within a day versus reuse across days, session estimates, private drafts, unchanged counts before confirmation, save-time catalogue/preference validation, stale revisions, CSRF and concurrent duplicate save protection. Existing authentication, nutrition, workout and migration tests also pass. Latest run details are in the [Open WebUI report](./openwebui-integration.md).
- **32 browser checks passed**, with eight screenshots at 1280 px and 375 px. Successful preview generation used a mocked provider in a temporary QA process; subsequent preview updates and confirmation used the actual running app and PostgreSQL. They verified zero plans before confirmation, one plan afterwards and zero workout logs. The temporary QA account and records were removed.
- The additive migration was applied locally and both web replicas are healthy.
- **Live checks, separately:** authenticated API access succeeded from both FitHub replicas. A synthetic read-only planner-tool request returned actual tool-execution evidence. The same model generated a valid synthetic workout draft through Open WebUI with tools disabled. These live requests are separate from the mocked suite and created no workout plans or logs. The previous offline-error screenshots remain historical checks of that error state.

## PowerShell commands

With the existing FitHub database and other services running:

```powershell
Set-Location E:\FitHub-School
docker compose build web nginx
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose up -d --no-deps --wait --wait-timeout 180 web nginx
docker compose ps
```

Run the mocked regression suite:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

Check authenticated Open WebUI model access from both replicas without printing the key:

```powershell
$probe = @'
from pathlib import Path
import httpx
key = Path('/run/secrets/fithub/openwebui-api-key.txt').read_text().strip()
r = httpx.get('http://host.docker.internal:3000/api/models', headers={'Authorization': 'Bearer ' + key}, timeout=5, trust_env=False)
print('Authenticated models HTTP', r.status_code)
'@
foreach ($replica in 1, 2) {
    $probe | docker compose exec --index $replica -T web python -
}
```

Keep the existing Open WebUI/LM Studio services running. The API key must already be present in the protected ignored file; do not paste it into commands, reports or the repository.

## Short browser checklist

1. Finish base onboarding, skip the optional step, and check that profile/bodyweight values are preserved and manual plan creation still works.
2. Open training preferences from Profile or Workout plans. Select days, minutes, equipment and exclusions; save and select Generate workout draft. With the AI offline, check the clear error and manual-builder link.
3. With the existing AI reachable, review an eligible private draft. Change a day name, exercise or sets; **Update preview** must not create a plan. Estimates must remain labelled and change with the set count.
4. Try saving without confirmation, then confirm **Save as new plan**. Only the confirmed action should create a plan, open the existing editor and leave workout history unchanged. Another account must not see the draft.

## Screenshots

| View | 1280 px | 375 px |
| --- | --- | --- |
| Training preferences | [Desktop](./screenshots/workout-ai/training-preferences-1280.png) | [Mobile](./screenshots/workout-ai/training-preferences-375.png) |
| Real AI-unavailable state | [Desktop](./screenshots/workout-ai/generation-unavailable-1280.png) | [Mobile](./screenshots/workout-ai/generation-unavailable-375.png) |
| Editable draft (mocked inference) | [Desktop](./screenshots/workout-ai/draft-preview-1280.png) | [Mobile](./screenshots/workout-ai/draft-preview-375.png) |
| Existing editor after confirmation | [Desktop](./screenshots/workout-ai/saved-plan-1280.png) | [Mobile](./screenshots/workout-ai/saved-plan-375.png) |
