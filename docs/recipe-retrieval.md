# FitHub recipe retrieval

Implemented on `ui/purple-theme`, entirely within FitHub. Existing Workout Review
routes, facts, validation, templates and scripts were preserved. The shared Open
WebUI client gained a separate nutrition call and configurable model lookup; the
existing workout calls retain their default settings and tests.

## Source and repeatable import

- Source: [farcrak/unitools-recipes](https://github.com/farcrak/unitools-recipes/tree/2f554073f7e730b18d707fe1164d18308f6ec16c).
- Pinned commit: `2f554073f7e730b18d707fe1164d18308f6ec16c`.
- Original file: [unitools-recipes-v1.json](../db/data/unitools/unitools-recipes-v1.json).
- Original file SHA256: `41fdc0572b7272ca557d2df22a0fac1cc1f18146e97ebd2c8258c395daf87d8a`.
- License SHA256: `28a9529c7d0bb4dc51f4bf5c116a3d16ef247a052f7591466768ddf563fd1cf5`.
- Attribution: **Recipe data: UniTools — theunitools.com, CC BY-SA 4.0**.
- Full [LICENSE](../db/data/unitools/LICENSE), [attribution/change notice](../db/data/unitools/ATTRIBUTION.md),
  [manifest](../db/data/unitools/manifest.json) and adapted
  [ingredient review metadata](../db/data/unitools/ingredient-review.json) are kept
  beside the source. The adapted recipe data remains CC BY-SA 4.0. Photos are not used.

The explicit 40-slug pilot covers higher-protein meals, breakfast options including
three at most 30-minute breakfasts, and batch meal preparation. Tags record higher
protein at 20 g or more per source serving and meal prep at four or more batch
servings. These labels describe curation; they do not prescribe personal targets.

The downloader uses only the pinned raw URLs and checks both hashes before replacing
existing files. The offline importer checks them again, validates the pilot and
stores stable `unitools:<slug>` IDs. A dry run performs no PostgreSQL writes and no
Chroma writes, even with `--index`. Repeat imports change no unchanged rows or IDs,
and preserve inactive status. Recipe changes and review metadata changes update by
content hash. Reindexing is an idempotent upsert of the same 40 public IDs. A failed
index operation can be retried without resetting either database.

## Storage and retrieval

Additive migration **009_recipe_retrieval** creates a shared `recipe_catalogue` and
an `include_catalogue` flag for existing private meal plans. Existing user recipes
and food/diary rows remain unchanged. PostgreSQL stores source IDs, servings,
ingredient IDs/names/quantities/original units/scaling/notes, instructions with step
times, per-serving macros, tags, provenance and content hashes.

Private recipe ownership and gram-based ingredients cannot safely represent shared
source recipes containing pieces, millilitres, pinches or “to taste”. A small
catalogue snapshot adapter therefore reuses existing exclusions, portions, totals,
meal-plan storage, edit/swap and explicit-save workflows. Source nutrition is kept
as per-serving nutrition and multiplied using Decimal. It is never reverse-engineered
into serving grams or per-100g rates. Source batch quantities and instructions remain
visible alongside the planned serving count and attribution.

Chroma uses FitHub's own existing `CHROMA_URL`, collection **fithub_recipes_v1**.
Public names, descriptions, tags and ingredient names are indexed with deterministic
256-dimensional SHA256 lexical token/bigram vectors. This requires no new dependency,
downloaded embedding model or external embedding API. It is lexical similarity,
not a claim of learned semantic retrieval. Index documents are built from an explicit
allowlist of public source fields; private recipes, users, preferences, diaries and
prompts are never indexed. User query text is embedded locally and is not persisted.

Eligibility is calculated in PostgreSQL-backed snapshots before vector ranking.
The Chroma query is restricted to already eligible IDs. Results are intersected with
that same eligible set, ignoring stale/foreign IDs. Missing meal-slot coverage is
filled only from already filtered rows; if no eligible recipe exists for a required
slot, generation stops. No exclusion is weakened to obtain a full day.

## Allergy and exclusion behavior

The existing saved allergy aliases, dietary tags and unwanted-ingredient labels are
used. Each imported ingredient needs an explicitly reviewed exact source name and,
when present, an approved preparation note before its declaration is complete.
Simple preparation notes are reviewed separately from unknown substitutes and
compound products. Eighty-four ingredient occurrences in this pilot remain incomplete.

Any active allergy rejects a recipe with any incomplete ingredient check. Known
allergen conflicts are rejected too. Unknown allergen labels stop generation. Ingredient
matching includes source IDs, names, reviewed aliases and name/note phrases; saved
dietary tags must be satisfied. These are declared-ingredient matching checks, not
cross-contact certification. Some restrictions can legitimately leave no full-day
pilot; the UI lists the blocking recipe constraints and keeps the manual planner available.

Restrictions and source eligibility are rechecked after inference and on edits/save.
The source must still be active and eligible. Current restrictions and the original
captured restrictions are both enforced. Model output cannot override exclusions.

## Model setting and backend-only credentials

**`NUTRITION_AI_MODEL=fithub-coach`** is separate from `WORKOUT_AI_MODEL`. It is
configured in FitHub's environment/Compose and defaults to the ID verified below.
No model ID comes from browser input. Existing endpoint, key-file mount, authentication,
concurrency limit and 120-second deadline are reused. Review and workout generation
retain their existing model configuration. Document tools are disabled for this call;
the eligible structured recipe data is already supplied by FitHub.

To verify the exact model ID using **FitHub's credentials**:

```powershell
Set-Location E:\FitHub-School
docker compose exec --index 1 -T web flask --app app nutrition-models
```

This uses the configured completion endpoint's origin, authenticated `GET /api/models`
and FitHub's server-side key file. It prints only model IDs and names, never keys,
headers or preset configuration. The verified entry was:

```json
{"id":"fithub-coach","name":"FitHub Coach"}
```

Read-only verification showed its existing base model is
`qwen2.5-7b-nerd-uncensored-v1.0`. The completion endpoint remains
`http://host.docker.internal:3000/api/chat/completions`. Credentials remain **[REDACTED]**.
If the ID differs in another installation, put the returned exact ID into FitHub's
`NUTRITION_AI_MODEL` setting and recreate the FitHub web replicas. Do not rename,
edit or recreate the Open WebUI preset.

## Planning contract and manual flow

The AI action is available from the existing Generate nutrition plan page and at
`/nutrition/meal-plans/ai`. GET is read-only; POST requires authentication and CSRF.
Only eligible public recipe IDs and structured source data, the meal-style request,
supported serving portions and saved targets are sent. User identity, credentials,
body measurements, diary history and private recipes are not included.

The response is a closed JSON object with exactly four named meal slots. Each slot
has its own eligible-ID enum and a portion string. IDs, slot compatibility, fields
and portions (0.25–2 servings in 0.25 steps) are validated server-side. Extra or invented
macros, arbitrary IDs, incorrect slots, nonfinite/out-of-range portions and malformed
responses are rejected before any draft is inserted. Totals are calculated from
stored source values; differences from manual targets remain explicit.

The existing private draft editor supports portions and eligible recipe swaps.
Only **Save plan** marks a draft saved. Neither generation nor saving logs diary
entries. Imported mixed-unit recipes deliberately do not expose automated gram-based
diary logging: source serving mass is unverified. Saved meals link to the existing
manual food logger, where consumed foods/grams can be recorded accurately. Existing
gram-based private/starter recipe logging is unchanged.

If Chroma or the provider is unavailable, or output is invalid, a clear error is shown
and no partial draft is created. The manual planner remains usable.

## Exact PowerShell commands

Re-download/check the pinned source if needed, then build the FitHub images:

```powershell
Set-Location E:\FitHub-School
.\scripts\download-unitools.ps1
docker compose build web nginx
```

Apply the additive migration, inspect the import without writes, import/index, and
recreate FitHub web/nginx without touching database or Chroma volumes:

```powershell
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose run --rm --no-deps -T web flask --app app import-recipes --dry-run --index
docker compose run --rm --no-deps -T web flask --app app import-recipes --index
docker compose up -d --no-deps --wait --wait-timeout 180 web nginx
docker compose exec -T nginx nginx -t
```

Verify the exact model ID and make a **live** synthetic call, without creating any
private plan or diary entry:

```powershell
docker compose exec --index 1 -T web flask --app app nutrition-models
docker compose exec --index 1 -T web flask --app app nutrition-smoke
```

`nutrition-smoke --allergy peanut --exclude chicken` optionally checks synthetic
constraints. An empty eligible meal slot reports failure before calling the provider;
restrictions are never relaxed. This may legitimately fail for the small pilot.

Run the full suite with real PostgreSQL isolation and **mocked inference/Chroma**:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

## Executed verification

- **239 Docker/PostgreSQL tests passed, zero skipped**, in **89.975 seconds** on the
  final full run. Eight new retrieval integration tests plus a separate nutrition
  model-setting test cover repeat import/dry run, source/license checksums, unknown
  ingredients, allergy and ingredient checks before ranking, scoped vector queries,
  unsupported IDs/slots/portions/fields, provider and search failures, preference
  changes during inference, source deactivation, totals, portions, explicit saving,
  no invented grams, ownership and CSRF. Existing Workout Review tests also passed.
- Deployed import/index contains **40 PostgreSQL recipes and 40 Chroma records**.
  Repeated dry runs from both replicas report **new: 0, changed: 0**. `nginx -t`
  passed and both web replicas, nginx and FitHub Chroma are healthy.
- **Separate live CLI check**, no inference mock: FitHub Coach returned a validated
  four-meal selection in **17.212 seconds** from 40 eligible public recipes and a
  12-recipe shortlist. Server totals were **2157.50 kcal, 148.50 g protein, 150.00 g
  carbs and 107.50 g fat**, with **zero database writes**. These are synthetic test
  results with unset targets, not recommendations.
- During live development, an unsupported meal-slot selection was rejected without
  creating a draft. The final contract uses slot-specific ID enums; it does not
  broaden recipe eligibility or trust model-reported nutrition.
- **22 browser checks passed at 1280px and 375px**, including a separate live Coach
  draft returned in **14.858 seconds**, unset targets, source attribution, portion
  edits, eligible swap choices, explicit save, no diary writes and fail-closed
  allergy exclusions. Browser verification used a temporary synthetic account
  against the rebuilt HTTPS app. The live model created a private draft for review;
  the account and its private
  plan are cleaned up afterward. Shared source records and the persistent demo are
  preserved. See the screenshots below for the retrieved draft and exclusions UI.

| View | Desktop | Mobile |
| --- | --- | --- |
| Recipe retrieval form | [1280px](screenshots/recipe-retrieval/retrieval-form-1280.png) | [375px](screenshots/recipe-retrieval/retrieval-form-375.png) |
| Live retrieved draft | [1280px](screenshots/recipe-retrieval/retrieved-draft-1280.png) | [375px](screenshots/recipe-retrieval/retrieved-draft-375.png) |
| Exclusions / empty shortlist | [1280px](screenshots/recipe-retrieval/retrieval-exclusions-1280.png) | [375px](screenshots/recipe-retrieval/retrieval-exclusions-375.png) |

The other AI workspace's repository, preset, tools, knowledge, containers and volumes
were not changed. No credentials or private exports were added to source control.
# Current collection

The pilot below is historical. See [recipe expansion](recipe-expansion.md) for the
current 100 measured recipes, retired pilot IDs, validation audit, matched target
comparison, tests and deployment commands.
