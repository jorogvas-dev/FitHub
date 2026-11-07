# Open WebUI workout integration verification

The latest release-wide live checks and current limitations are in
[submission verification](submission-verification.md). Older results below are
historical observations, not a claim about present service availability.

Verified locally on 2026-11-07. API key: **[REDACTED]**.

The optional [nutrition draft flow](./nutrition-generation.md) now uses FitHub's existing rule-based meal planner and catalogue-backed starter recipes. Its tests, screenshots and rebuild command are documented separately.

## Duplicate-exercise correction

The model could return two prescriptions with the same exercise ID in one day, including rows with different sets or rep ranges. The server correctly rejected the draft, but generation previously stopped without giving the model feedback.

The adapter now validates the generated response and allows **one correction attempt** for an invalid, bounded response. Duplicate feedback identifies the one-based training day and repeated exercise ID. The model must return a complete corrected draft using the same survey, eligible catalogue and schema. The prompt explicitly allows the same exercise on different days while requiring one prescription per exercise within each day. The schema also caps exercise rows by the number of supplied eligible IDs, up to eight.

Both attempts share the **same original inference deadline**; a correction never starts a fresh two-minute wait. Authentication, transport and timeout failures are not retried. Oversized responses are not included in correction requests. Invalid prescriptions are not silently dropped or merged. The corrected response must pass every existing exercise, equipment, exclusion, prescription and duration check. A second invalid response produces a clear error and retains the manual builder. Preview edits and confirmed saves still reject duplicates, and no invalid draft is persisted.

**179 Docker/PostgreSQL tests passed**, zero skipped, in **61.844 seconds**. All 23 focused adapter/generator tests passed. A failing duplicate-generation regression was reproduced before the fix. Added checks cover duplicate IDs with different prescriptions, precise day/ID feedback, cross-day reuse, bounded retry count and shared deadline, invalid corrections, no writes after failed correction, and duplicate rejection during both preview updates and confirmed saves. Regression inference is mocked.

**Separate live correction smoke:** a known synthetic two-day draft with a repeated exercise ID in day 1 was injected as the initial response. The correction request then used the actual existing Open WebUI/Qwen model, which returned a valid two-day draft in **13.448 seconds**. Both days used distinct IDs within the day and reused IDs across days correctly. Estimated durations were 20 and 18 minutes. This targeted check used two real eligible bodyweight catalogue entries and made **zero database writes**. Its initial response was deliberately injected; it is not a claim of two live model calls.

Both web replicas were rebuilt and restarted with this correction path. No new model, dependencies or database migration were added. Explicit confirmed **Save as new plan** remains the only action that creates a plan.

**Deployed browser check:** normal live generation through the HTTPS form produced a valid three-day draft in **27.458 seconds** using a synthetic profile and 48 eligible real catalogue exercises. All three days contained distinct exercise IDs. **22 browser checks passed**, including explicit unchecked save confirmation, private draft storage, zero plans/workout logs, waiting/duplicate-submit behavior, back navigation, and 1280 px / 375 px layouts. The QA account and draft were removed afterwards. This was ordinary generation through the deployed adapter; the targeted live correction check above was separate.

| Latest view | 1280 px | 375 px |
| --- | --- | --- |
| Waiting state (navigation paused to inspect the UI) | [Desktop](./screenshots/workout-ai/generation-duplicate-fix-waiting-1280.png) | [Mobile](./screenshots/workout-ai/generation-duplicate-fix-waiting-375.png) |
| Validated live-model draft | [Desktop](./screenshots/workout-ai/generated-duplicate-fix-live-1280.png) | [Mobile](./screenshots/workout-ai/generated-duplicate-fix-live-375.png) |

## Timeout correction

An ordinary synthetic **three-day, 45-minute** survey with **48 eligible real catalogue exercises** returned a valid plan in **36.439 seconds** when measured with a standalone 150-second probe. FitHub's former 20-second deadline rejected it before completion; the proxy also had a 30-second read deadline. The diagnostic probe created no database records.

The generation deadline is now **120 seconds by default**, configurable from **1–180 seconds**. The exact `/plans/generate` proxy route allows **195 seconds** so the app can return its own bounded timeout error; other routes retain their 30-second read timeout. Gunicorn's worker timeout is **210 seconds**. Both replicas were rebuilt and verified to use 120 seconds. Endpoint, model, credentials and inference setup are unchanged.

The generate button now disables during submission, shows an accessible waiting message and blocks repeat submissions in that page. Back navigation restores the button. A successful response still goes through the existing server validation and private preview; only explicit confirmed **Save as new plan** creates a plan.

The updated mocked Docker/PostgreSQL suite passed **170 tests**, zero skipped, in **56.004 seconds**, including the longer valid wait budget, its hard cap, existing timeout/error paths and confirmation safeguards. All 16 focused adapter/generator tests passed. `nginx -t` passed before and after deployment.

**Real browser verification:** a temporary synthetic account completed onboarding with three 45-minute sessions and gym equipment. Native form submission through `https://localhost:8443/plans/generate` used the live model and 48 eligible catalogue exercises, then opened an editable three-day draft in **33.984 seconds**, exceeding both former deadlines. Database counts were **one private draft, zero plans and zero workout logs**. App health responded during inference in 2.144 seconds. All **18 browser checks** passed, including the waiting status, repeat-submit protection, a single generation POST, estimated durations, back-navigation recovery and layouts at 1280 px / 375 px. The temporary account and its records were removed.

The waiting-state screenshots exercise the real UI handler with navigation paused locally; the draft screenshots come from the separate successful live generation above. All displayed profile and workout data belongs to the synthetic test account.

| View | 1280 px | 375 px |
| --- | --- | --- |
| Generating state | [Desktop](./screenshots/workout-ai/generation-waiting-1280.png) | [Mobile](./screenshots/workout-ai/generation-waiting-375.png) |
| Validated live-model draft | [Desktop](./screenshots/workout-ai/generated-live-1280.png) | [Mobile](./screenshots/workout-ai/generated-live-375.png) |

## Connection and configuration

Nutrition plans now fit current daily targets as exact hard limits. See
[nutrition-target-limits.md](nutrition-target-limits.md) for scoring, optional snacks,
validation across mutations, migration/deployment commands and separate live checks.

Recipe retrieval uses the separate FitHub `NUTRITION_AI_MODEL` setting. See
[recipe-retrieval.md](recipe-retrieval.md) for pinned source/license checksums,
import and indexing commands, exact preset-ID verification with FitHub credentials,
and the separate live FitHub Coach result. The existing workout/review model remains unchanged.

AI Workout Review reuses this provider, model, credential and bounded deadline.
See [workout-review.md](workout-review.md) for the validated response contract,
mocked regression coverage, separate live smoke result and rebuild commands.

FitHub's [adapter](../app/services/workout_ai.py) now uses authenticated Open WebUI chat completions. Both web replicas share the backend configuration in [Compose](../compose.yaml).

| Item | Verified value |
| --- | --- |
| Installed Open WebUI | `0.12.5` |
| Host UI/API origin | `http://localhost:3000` |
| FitHub Docker completion endpoint | `http://host.docker.internal:3000/api/chat/completions` |
| Model | `qwen2.5-7b-nerd-uncensored-v1.0` |
| Existing Open WebUI upstream | `http://host.docker.internal:1234/v1` (LM Studio; unchanged) |
| API authentication | Bearer token from a backend-only file; value **[REDACTED]** |
| Inference deadline | 120 seconds by default; backend accepts 1–180 seconds |
| Workout tool IDs | Empty by default; workout generation explicitly sends `tool_ids: []` and `tools: []` |

The existing model and inference configuration were retained. FitHub calls Open WebUI, which uses its existing LM Studio connection. The configured model appears in the authenticated model list; the planner preset `qwen-planner-v05a` uses the same base model and attaches `dynamic_plan_validator_v0_5a`.

`WORKOUT_AI_ENDPOINT`, `WORKOUT_AI_MODEL`, `WORKOUT_AI_TOOL_IDS` and `WORKOUT_AI_TIMEOUT_SECONDS` are backend settings. Tool IDs are a bounded, validated comma-separated list, never browser inputs. The credential is stored in the protected ignored `.secrets/openwebui-api-key.txt` and mounted read-only at `/run/secrets/fithub/openwebui-api-key.txt`. Git and the Docker build context exclude that directory. Headers, keys and provider error bodies are not echoed in user-facing errors.

## Authentication from both replicas

| Request | Replica 1 | Replica 2 |
| --- | --- | --- |
| Unauthenticated `GET /api/models` | 401 | 401 |
| Authenticated `GET /api/version` | 200; `0.12.5` | 200; `0.12.5` |
| Authenticated `GET /api/models` | 200; existing Qwen available | 200; existing Qwen available |
| Authenticated `GET /api/v1/tools/` | 200 | 200 |

## Actual installed workspace tools

| Tool ID | Installed purpose |
| --- | --- |
| `document_analysis_production` | `analyze_document(user_request: str)`; document facts/analysis, not workouts |
| `dynamic_plan_validator_v0_5a` | Read-only validation and preview of legacy v0.5a task plans |
| `workspace_file_access` | Mixed file read/list/write and script/workflow capabilities; not enabled for these tests |
| `document_service` | Document extraction; not used |
| `knowledge_search` | Knowledge search and document indexing; not used |

The existing planner is **not a workout planner**. Its tool entry point is:

```python
validate_dynamic_plan(proposed_plan: str, correction: str = "") -> str
```

`proposed_plan` is a JSON string with `version: "0.5a"`, declared `variables` and dependency-ordered `steps`. Supported operations are `search_knowledge`, `extract_number`, `calculate`, `write_report` and `verify_file`. An optional correction is a separate JSON string. The returned JSON string contains `valid`, `version`, validation details/errors, capabilities, and a nonpersistent preview. This task/document workflow contract does not accept FitHub's training days, exercise IDs, sets and rep ranges. The separate production analysis tool is document-only. Neither is attached to workout generation.

## Live read-only tool smoke test

A request from FitHub replica 1 used the existing Qwen model and only `dynamic_plan_validator_v0_5a`. Its synthetic input described adding two counts of three sets, rendering an in-memory preview and checking that preview. No user records or private documents were supplied.

Open WebUI 0.12.5's nonstreaming native tool-call response does not by itself prove execution. The smoke request used `params: {"function_calling": "legacy"}`. Open WebUI actually invoked the installed tool and returned a top-level `sources` record with `tool_result: true`. The evidence came from that server-generated record, not from the assistant's summary:

```json
{
  "tool_result": true,
  "source": {
    "name": "dynamic_plan_validator_v0_5a/validate_dynamic_plan"
  },
  "document": [{
    "valid": true,
    "version": "0.5a",
    "validation": {"step_count": 3, "valid": true},
    "preview": {
      "execution": "not performed",
      "persistent_effects": [],
      "network_effects": [],
      "approval_records": []
    }
  }]
}
```

The excerpt decodes the JSON string in `document` and omits other validation/preview fields. HTTP status was **200**, elapsed **30.160 seconds**. The standalone smoke client allowed 60 seconds; FitHub still had its former 20-second generation deadline at that point. `execution: "not performed"` refers to the proposed plan operations: the validator tool itself executed, but it did not write the proposed report, retrieve knowledge, create approvals or execute a workflow.

For future compatible backend tools, the adapter uses this installed server execution path and requires an actual returned tool-result record. A native `tool_calls` response or a model assertion alone is rejected. The legacy validator remains disabled for workouts.

## Initial live workout smoke tests, before the timeout correction

- A synthetic profile (180 cm, 80 kg, beginner, general fitness), one 30-minute day, and two supplied bodyweight exercise entries produced a valid workout through the same model with tools disabled. FitHub validated the IDs, sets, reps and duration. The returned day used two sets each of squat and push-up, 8–15 reps, with a recalculated **15-minute estimate**. Elapsed **6.958 seconds**. No database writes, plans or workout logs were created.
- After the final rebuild, the same small synthetic check passed from replica 2 in **6.824 seconds**: squat 4 × 8–12 and push-up 3 × 10–15, with a recalculated **23-minute estimate**. Authenticated model/tool access was rechecked successfully from both final replicas.
- A larger synthetic-profile check using **10 eligible exercises from the real FitHub catalogue** returned an over-budget day. FitHub rejected it correctly (15.360 seconds). Explicitly including the computed daily set budget in the request did not eliminate this model failure: a repeat was also rejected (17.487 seconds).
- A separate 60-minute synthetic survey with the same eligible real catalogue hit the configured timeout (20.463 seconds observed including local script overhead). No draft or plan was saved.

There is **no remaining endpoint, authentication or model-discovery blocker**. The existing planner contract cannot generate FitHub workout JSON, so direct structured model generation through Open WebUI is used. The short deadline behind the initial larger-request timeout was corrected above. A model can still return an invalid session estimate or exceed the new bounded deadline; FitHub rejects those responses, shows the existing error/manual-builder path and retains the explicit confirmation requirement.

## Regression and workflow safeguards

The initial connection run passed **169 Docker/PostgreSQL tests**, zero skipped, in **57.757 seconds**. The timeout fix raised coverage to **170 passing tests** as recorded above. Inference and provider HTTP responses are mocked in this suite; real model calls are separate live checks.

Coverage includes missing/invalid credentials, 401/403 responses without secret leakage, Authorization headers, backend model/tool configuration, timeouts, malformed envelopes and false tool-execution claims. Existing workout tests still cover skipped onboarding, preserved profiles/bodyweight, inactive/unknown/excluded/timed exercises, unsupported equipment, invalid sets/reps, private previews, revalidation on save, explicit confirmation, stale revisions and duplicate-save protection. Only **Save as new plan**, with confirmation, creates a plan. Generation and preview edits create no workout logs.

Both replicas were rebuilt and restarted with the updated adapter. No additional schema migration was needed for this connection change. No dependency or model installation was added.

## PowerShell commands

Rebuild and restart FitHub with the existing protected key file and AI services:

```powershell
Set-Location E:\FitHub-School
docker compose build web nginx
docker compose up -d --no-deps --wait --wait-timeout 180 web nginx
docker compose ps web nginx
```

Run the mocked suite against isolated PostgreSQL test schemas:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

The [workout feature guide](./workout-generation.md#powershell-commands) includes an authenticated API probe for both replicas that prints status codes only.

Browser checklist:

1. Complete or edit training preferences, then open **Generate workout plan** from Workout plans.
2. Review a successful private draft and edit it; **Update preview** must not create a plan. Check that minutes remain labelled as estimates.
3. Confirm **Save as new plan** explicitly; check the existing editor opens and workout history is unchanged.
4. With the AI unavailable or an invalid response, check the clear error and working manual-builder link. Another account must not see the draft.
