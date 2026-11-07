# Exact nutrition target fitting

Implemented on `ui/purple-theme`. Saved daily calories, protein, carbohydrate and
fat targets are independent **hard upper limits** for nutrition plans. A missing
target imposes no limit; an explicit zero permits no positive amount of that nutrient.
The user's saved targets are never changed or inferred from other targets.

## Server selection and portions

Existing recipe retrieval and server-side allergy, ingredient and dietary exclusions
run before model selection. Ingredients with incomplete review still block a recipe
under an active allergy. Recipes missing a nutrient needed to check a configured
limit are rejected before being offered to the model. Unknown nutrients with no
configured limit remain unknown in selected meals and totals; they are not zeros.

FitHub Coach returns validated recipe/portion preferences from the eligible shortlist.
The server then selects meals and adjusts portions independently of those proposals.
The Coach cannot authorize an overage. Its choices only influence deterministic
search preference/tie ordering; all feasibility checks and final amounts are computed
from stored recipe/food data with Decimal arithmetic.

The objective is the sum of squared normalized shortfalls across configured targets:

```text
sum(((target - planned_total) / target) ** 2)
```

Every state must satisfy `planned_total <= target` before scoring. Zero targets use
a denominator of one, but feasible states must already contain exactly zero for
that nutrient. Fractional positive targets use their actual value as denominator,
so calories and grams are comparable even for targets below one.

The practical bounded search considers up to **20 eligible recipes per meal slot**,
**0.25–2 servings in 0.25 steps**, and **128 retained states** at each main-meal stage.
Progressive intermediate scoring keeps main-meal prefixes near 30%, 65% and 100% of
the targets while retaining an empty state for later feasible meals. Final selection
minimizes the full normalized shortfall. Stable recipe/portion ordering makes ties
deterministic; coverage is preferred on equal scores. This is a bounded heuristic,
not a guarantee of the global optimum across every recipe combination.

Breakfast, lunch and dinner are considered first. A slot may be omitted when no
supported portion fits the constraints, and partial days are labelled. If no nonzero
portion fits, the preview is empty with a clear explanation. A partial/empty result
does not relax limits, invent foods or turn missing nutrition into zero.

## Optional snack

After the main meals, the server evaluates an optional snack from eligible retrieved
recipes, private snack recipes and FitHub's verified starter snack templates backed
by active Food library ingredients. These snack candidates pass the same exclusions
and configured-nutrient completeness checks. Private snack recipes are considered
only server-side; they are not added to the shared Chroma index or sent in the public
recipe model request.

A snack is included **only when it strictly reduces normalized shortfall and the
combined day's exact totals stay within every limit**. Existing meals may be adjusted
within the same portion grid. No snack is added when all targets are unset or when
it cannot improve the fit. The 0.25–2-serving bounds also apply to snacks.

Conflicting target ratios, recipe availability and portion granularity can leave
gaps. The preview lists the remaining shortfall and search limitations, retains all
targets as entered, and never describes an over-limit plan as acceptable.

## Exact quantities and current-target validation

- Per-serving source macros and gram-based recipe arithmetic use stored values
  and Decimal throughout. There is no floating-point or display-rounding tolerance.
  For example, **100.004 kcal is rejected against a 100 kcal limit** even if both
  would display as 100 in the normal whole-number formatting.
- The earlier physical-gram quantization was removed. Batch grams are multiplied
  by portion and divided by batch servings without centigram quantization. Display
  formatting remains separate. Decimal's existing arithmetic context handles
  recurring divisions; there is no extra application rounding step.
- Additive migration **011_exact_nutrition_limits** widens only diary gram storage
  to unconstrained-scale `numeric`, retaining its existing quantity range checks.
  Existing row values are preserved. This prevents PostgreSQL from silently rounding
  generated fractional grams on explicit logging.
- Generated fractional grams load into diary edit forms at full precision and can
  be saved unchanged. Normal manually entered gram quantities retain the existing
  two-decimal validation. Nutrient calculations, old diary history, profile and
  bodyweight values are not reformatted in storage.
- Plan totals, current upper limits and remaining amounts are shown together. Unknown
  totals remain unknown. Original generation targets remain in plan metadata, while
  current saved settings govern all new writes.

The same validator now covers manual and AI generation, generation of another draft,
portion/recipe edits, swap previews, confirmed replacements and confirmed saving.
Each edit or replacement validates the **whole projected day**, including the meals
being retained, rather than just checking the selected meal.

Save and edit forms have signed, expiring previews bound to the plan's meal versions,
portions, recipe snapshots, current targets and effective exclusions. Swap tokens
also include that full-plan context. Changed targets, exclusions, other meals or
stale versions require refreshing the preview (409). Malformed previews return 400.
Current targets and exclusions are checked again after acquiring the mutation locks.
Settings writes and planner mutations share a per-user lock to prevent a target
update from slipping between validation and commit.

Explicit Save plan still controls confirmed saving. Accepting a remaining shortfall
does **not** authorize an overage or an unknown capped total. Saving or generating
does not log food. Existing over-limit plans/history are retained; current limits
prevent unsafe edits/saves and users can generate a fresh draft with current targets.
Diary logging records actual consumption separately from planner target fitting.

## Verification

**266 Docker/PostgreSQL tests passed, zero skipped**, in **98.346 seconds** on the
final full run. Inference and retrieval calls in the regression suite are mocked.
Fifteen new focused tests cover:

- inclusive exact limits and fractional overages hidden by display rounding;
- normalized calorie/gram scoring, including fractional targets below one;
- exact feasible fits and exhaustive comparison for a small search space;
- conflicting/zero/missing targets, partial results and unchanged saved targets;
- unknown capped versus uncapped nutrients;
- optional snack addition, strict improvement and rejection when another cap would
  be crossed; current exclusions and incomplete allergy checks for snack candidates;
- server fitting of deliberately oversized model portions;
- whole-day cap checks in swaps and portion edits;
- stale save/edit/swap previews after changed targets;
- precise fractional gram logging and unchanged edit-form round trips.

Earlier tests that required four meals with an automatic snack or accepted overages
were updated to assert the new optional-snack and hard-limit behavior. Existing
ownership, exclusions, provider-failure, concurrent confirmation, diary preservation,
recipe import, workout, Personal Records, Weekly Recap and Workout Review tests passed.

### Separate live Open WebUI checks

No inference mocks were used for these checks. Both used the existing authenticated
**`fithub-coach`** preset, endpoint and model connection; credentials remain server-side.

1. Live CLI: **13.526 seconds**, zero database writes. Limits were 2000 kcal,
   140 g protein, 230 g carbs and 65 g fat. Exact server totals were 1837.600000 kcal,
   119.956000 g protein, 205.176000 g carbs and 64.892000 g fat. A verified
   `starter:banana-berries` snack improved fit without crossing any cap.
2. Live HTTPS browser generation and save: **20.334 seconds** to the fitted preview,
   followed by **18 passing browser checks** at 1280px and 375px. It produced:

| Nutrient | Configured upper limit | Exact planned total | Exact remaining |
| --- | ---: | ---: | ---: |
| Calories | 2000 | 1912.60000000 | 87.40000000 |
| Protein | 140 | 117.95600000 | 22.04400000 |
| Carbohydrate | 230 | 224.67600000 | 5.32400000 |
| Fat | 65 | 64.39200000 | 0.60800000 |

The browser preview included the eligible banana/berries snack, showed all current
limits and remaining amounts, rejected an old save after the calorie limit changed,
disabled confirmed saving of the refreshed over-limit draft, and allowed fresh
explicit confirmation once feasible settings were restored. Targets remained exactly
as entered and the diary stayed empty. The temporary synthetic account and plans
were removed afterward; the persistent demo and shared recipe index were preserved.
Different live proposals can produce different feasible results; all pass the same
server checks.

| View | Desktop | Mobile |
| --- | --- | --- |
| Server-fitted live preview | [1280px](screenshots/nutrition-limits/fitted-preview-1280.png) | [375px](screenshots/nutrition-limits/fitted-preview-375.png) |
| Changed limits block saving | [1280px](screenshots/nutrition-limits/changed-limits-1280.png) | [375px](screenshots/nutrition-limits/changed-limits-375.png) |
| Confirmed feasible plan | [1280px](screenshots/nutrition-limits/saved-feasible-plan-1280.png) | [375px](screenshots/nutrition-limits/saved-feasible-plan-375.png) |

## Exact PowerShell deployment commands

With the existing FitHub database, Chroma and nginx running:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose up -d --no-deps --wait --wait-timeout 180 web
```

No dependency installation, recipe reimport, index reset, credential change or volume
deletion is needed. Both web replicas were rebuilt and became healthy; migration
rerun reports up to date.

Run the complete mocked regression suite in isolated PostgreSQL schemas:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

Run a separate live, synthetic, read-only Coach verification:

```powershell
docker compose exec --index 1 -T web flask --app app nutrition-smoke --kcal 2000 --protein 140 --carbs 230 --fat 65
```

Omit a CLI target option to leave it unset. `--allergy` and `--exclude` can supply
synthetic restriction labels. The command prints exact fitted totals and notes,
creates no private draft, and never prints the API credential.
