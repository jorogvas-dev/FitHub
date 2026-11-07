# Swap this meal

Implemented on `ui/purple-theme` for each meal in an owned, saved nutrition plan.
Existing recipe retrieval, exclusions, serving portions, Decimal calculations,
plan presentation and FitHub Coach credentials/configuration are reused. Workout
Review and other AI project configuration were preserved.

## Comparisons and ranking

The current meal is compared at its saved portion. All candidates come from the
active shared FitHub recipe catalogue and must support the selected meal slot.
The current stable recipe ID is excluded, including at different portions.

Before ranking, existing server-side allergy, unwanted-ingredient and dietary
checks are applied. Both the user's current restrictions and the plan's captured
restrictions are enforced. An active allergy rejects any candidate with incomplete
ingredient review metadata. No model or vector result can relax these checks.

The calorie tolerance is **±max(50 kcal, 15% of the current meal's calories)**.
This applies to all three comparisons to keep portions reasonably comparable:

- **Similar calories:** minimize absolute calorie difference.
- **More protein:** require a strict increase in protein and prioritize the largest
  protein increase within the calorie tolerance.
- **Quicker to prepare:** require a strictly shorter stored total of preparation
  plus cooking minutes; prioritize the shortest batch time within the calorie
  tolerance. Zero minutes is valid; missing preparation/cooking values are unknown.

For each eligible recipe, all supported portions (0.25–2 servings in 0.25 steps)
are evaluated using existing nutrition calculations. Only its best portion is kept.
Ties use calorie distance, distance from the current portion, portion, and stable
recipe ID. At most three distinct recipes are shown. This numeric search reads the
same PostgreSQL recipe catalogue used by retrieval; narrowing it with vector
similarity would unnecessarily discard potentially better numeric alternatives.
No private data is added to Chroma.

Existing automatic-recipe eligibility requires complete nutrition. Missing values
needed for a comparison also prevent that comparison: unknown current calories
disable comparison; unknown current protein disables More protein; unknown current
preparation disables Quicker. Candidates missing preparation are omitted from
Quicker. Empty results explain that no eligible option meets the comparison and
leave the selector available for another choice.

Each alternative displays its portion, ingredient names, calories/macros, differences
from the current meal, original batch preparation time when known, and source
quantities/instructions/attribution. Values are calculated from stored data before
display rounding. Preparation time describes the full original recipe batch and
is **not scaled linearly with the chosen serving portion**.

## Optional Coach explanations

Ordinary preview GETs do not call inference. **Explain with FitHub Coach** is a
separate CSRF-protected POST using the existing `NUTRITION_AI_MODEL`, endpoint,
credential file, bounded concurrency and deadline. It sends only server-calculated
alternative facts and allowed explanation IDs. Tool use is disabled.

The model can select an applicable emphasis (calorie match, protein gain or time
saved) for known alternatives. Closed structured validation rejects unknown recipe
references, duplicate references, extra fields and unsupported reasons. All rendered
wording comes from server-owned explanations; the model cannot supply unchecked
numbers, text, portions or rankings. Provider failure or invalid output shows
**Coach explanation is unavailable. The alternatives are ready to use.** The same
server-ranked alternatives and replacement controls remain available.

## Confirmation, ownership and history

Previewing makes no database changes. Each candidate has a signed, 30-minute preview
token bound to the signed-in user, plan, meal, meal version, current snapshot/portion
hash, selected recipe/portion, source snapshot hash and comparison mode. The token
does not expose private recipe contents or credentials.

**Replace meal** requires CSRF and an explicit confirmation checkbox. The server
locks the owned plan and selected meal, checks saved status and the current version,
reapplies current/captured exclusions, recalculates comparison eligibility, checks
the allowed portion and verifies that the source snapshot matches the preview.
No browser-submitted recipe, nutrient amount or portion is trusted.

Only the selected `meal_plan_meal` row is replaced and its version advances once.
Plan totals and target differences recalculate through the existing plan view.
The saved plan's status, date, captured targets/restrictions and other meals remain
unchanged. A stale/expired preview, changed source, newly ineligible candidate or
repeated confirmation returns **409** with a refresh instruction. Tampered or
wrong-meal tokens return **400**; another account's routes return **404**.
Simultaneous confirmations serialize and produce one successful replacement and
one conflict. Existing draft edits also advance the meal version.

Diary entries and their nutrient/name/quantity snapshots are never modified or
deleted. The existing logged marker and diary links remain, so a previously logged
meal cannot be logged again through the plan after swapping. Saved-plan UI explains
that its diary history stays unchanged. Existing manual food logging remains separate.

## Additive migration and import

Migration **010_meal_swaps** adds a positive meal version (existing meals start at
1) and nullable preparation/cooking columns to the public catalogue. The repeatable
source importer backfills the pinned upstream `prepMinutes` and `cookMinutes` and
includes them in source snapshots/content hashes. Existing private plan snapshots
are not rewritten.

An older catalogue snapshot without times may use stored preparation data from
the **same recipe ID and exact pinned source commit**. Missing data or a different
revision is never inferred from recipe names or summed instruction steps. Private
recipes without stored preparation times can still use calorie/protein comparisons.

## Executed verification

- Full Docker/PostgreSQL suite: **251 tests passed, zero skipped, 77.329 seconds**.
  All inference in this suite is mocked. Twelve focused swap tests cover scaled
  portions, the calorie tolerance, strict protein/time comparisons, zero and missing
  times/nutrition, legacy preparation handling, exclusions and unknown ingredients,
  ownership/CSRF/saved-only access, no writes on preview, selected-meal-only changes,
  total recalculation, source changes/deactivation, changed exclusions, forged
  portions/tokens, expiration, stale versions, repeated and simultaneous confirmations,
  unchanged diary snapshots/markers, and failed/fabricated Coach explanations.
- **52 browser checks passed** at **1280px and 375px**, including all three selectors,
  up to three alternatives, ingredients/macros/differences, no document/card overflow,
  no preview writes, explicit confirmation, unchanged diary data and other meals,
  one version increment, and a side-effect-free 409 on repeated confirmation.
- **Separate live Open WebUI check:** an explicit Explain with FitHub Coach request
  returned **three validated explanations in 6.285 seconds**. This call used the real
  existing preset and was not mocked. The browser account, saved plan and diary row
  were synthetic, and were removed afterward. Existing demo/user data was preserved.
- Both rebuilt web replicas and nginx are healthy; `nginx -t` passed. Migration
  rerun reports up to date. The reimport retained all 40 stable recipe IDs and
  backfilled their preparation data; a subsequent dry run reports zero new/changed.

| View | Desktop | Mobile |
| --- | --- | --- |
| Similar calories | [1280px](screenshots/meal-swaps/swap-similar-1280.png) | [375px](screenshots/meal-swaps/swap-similar-375.png) |
| More protein | [1280px](screenshots/meal-swaps/swap-protein-1280.png) | [375px](screenshots/meal-swaps/swap-protein-375.png) |
| Quicker | [1280px](screenshots/meal-swaps/swap-quicker-1280.png) | [375px](screenshots/meal-swaps/swap-quicker-375.png) |
| Live Coach explanation | [1280px](screenshots/meal-swaps/swap-coach-1280.png) | [375px](screenshots/meal-swaps/swap-coach-375.png) |
| Replaced planned meal | [1280px](screenshots/meal-swaps/swapped-plan-1280.png) | [375px](screenshots/meal-swaps/swapped-plan-375.png) |

## Exact PowerShell migration/rebuild commands

With the existing FitHub database and Chroma services running:

```powershell
Set-Location E:\FitHub-School
docker compose build web nginx
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose run --rm --no-deps -T web flask --app app import-recipes --index
docker compose up -d --no-deps --wait --wait-timeout 180 web nginx
docker compose exec -T nginx nginx -t
```

Reimport is needed to populate preparation times; it does not reset recipes or
user data. No volume deletion, dependency installation, API key change, model
change or other-project update is needed.

To rerun the mocked regression suite:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

For a live check, log into FitHub, open a saved nutrition plan, select **Swap this
meal**, choose a comparison and optionally click **Explain with FitHub Coach**.
Inspect values, confirm **Replace meal**, and verify the plan updates while diary
history stays intact. A regular preview requires no model connection.
