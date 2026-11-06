# Nutrition, recipes and rule-based meal planning

Open https://localhost:8443/nutrition after signing in. Navigation provides Daily diary,
Food library, My foods, My recipes, Meal plans, and Preferences & targets. Recipes,
plans, settings and diary entries are private. Food library is shared; only database
administrators can edit or deactivate shared records.

The diary, food selection and meal plans now share the dashboard workspace design.
See [nutrition UI verification and screenshots](./black-violet-theme.md) for the current
presentation-only rebuild command and desktop/mobile checks; no new migration is
required for that UI update.

## Update the existing installation

Run PowerShell with the existing database and Chroma containers available:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose up -d --no-deps --wait --wait-timeout 180 web
```

Migration `004_food_catalogue` adds shared foods, independently reviewed ingredient
metadata and nutrition provenance, and widens existing nutrient columns without
changing their values. It bundles 100 USDA records. Migration `005_meal_planner`
adds private recipes, ingredients, drafts, saved plans and duplicate-log protection.
Migration `006_usda_source_links` replaces the retired USDA per-food route with the
verified downloads page; permanent food IDs and exact archive URL remain recorded.
Existing food and diary IDs, ownership constraints, accounts and fitness data stay
intact. There are 19 public tables including the migration ledger. Fresh setup runs
[04-food-catalogue.sql](../db/init/04-food-catalogue.sql) and
[05-meal-planner.sql](../db/init/05-meal-planner.sql) and
[06-usda-source-links.sql](../db/init/06-usda-source-links.sql), recording the same versions.
Repeating db-upgrade is a no-op. The existing deployment updater also applies these
migrations. No dependencies, secrets, volumes or deployment settings change.

To repeat the bundled import offline (insert missing source IDs only):

```powershell
docker compose run --rm --no-deps -T web flask --app app seed-foods
```

Administrator access uses the existing explicit promotion command; no password or
account is created and registration cannot grant permissions:

```powershell
docker compose run --rm --no-deps -T web flask --app app promote-admin "existing_username"
```

## Shared Food library and private My foods

Search both collections or filter to either one. Sort calories, protein, carbs or fat per 100 g, lowest or highest first. Sorting uses full-precision numeric values, puts unknowns last in both directions, and breaks ties by case-insensitive name then collection/ID. The GET form preserves search, collection, preparation and selected diary date. Library preparation filters include
raw, uncooked/dry, cooked and as described. Original USDA descriptions retain fat
percentages, preparation and edible-part qualifiers (for example drained solids,
peeled cucumber or separable lean meat). Enter grams of the stated preparation's
edible portion; no cups, serving conversions or millilitres are inferred.

The bundled subset comes from the official [USDA FoodData Central downloadable
SR Legacy April 2018 final release](https://fdc.nal.usda.gov/download-datasets/).
The [USDA download field definitions](https://fdc.nal.usda.gov/docs/Download_Field_Descriptions_Oct2020.pdf)
define permanent FDC IDs and nutrient amounts per 100 g in their stated units.
We import nutrient IDs 1008 (energy, kcal), 1003 (protein, g), 1005 (carbohydrate,
g) and 1004 (total lipid, g). No calorie estimate fills missing source energy.
Source zero remains zero; absent or blank amounts remain NULL.

The checked-in [seed](../db/data/usda-sr-2018.json) stores source descriptions, FDC
IDs, source URLs, dataset version, download URL, publication dates and the
archive SHA256. Administrator source links open the verified downloads page (the former
per-food route returns 404); individual FDC IDs are displayed alongside the link,
and the exact archive URL is retained in provenance. Member food selection, logging and private food editing omit source citations and external links; administrator editing retains them. Publication dates in this FDC export can be 2019-04-01 even though
the source release is the final April 2018 SR Legacy dataset. The curated selection
is [pinned by ID and exact description](../db/data/usda-selection.json).
Archive SHA256: `b80817294b8850530aaedf2e515c02593b1824f763a0ff356e5c2081643e6fd0`.

Rebuild the JSON from the official downloaded CSV ZIP using host Python (standard
library only):

```powershell
python scripts/build_usda_seed.py C:\Downloads\FoodData_Central_sr_legacy_food_csv_2018-04.zip
```

The importer checks units, descriptions, finite values, bounds and precision. The
versioned SQL contains the same initial subset; changing a future curated release
requires a new migration, rather than rewriting an already applied migration.
Normal application use, fresh installation and seed-foods need no API key or network.

Shared records are never deleted through the UI. Deactivation removes them from new
diary/ingredient choices and excludes dependent recipes from new plan generation.
Existing snapshots remain viewable and saved plans can still be logged from their
captured values, subject to original/current restrictions. Repeat seeding preserves
existing IDs, administrator edits and inactive status. Users may copy an active
library food into an editable private food; the full original description remains
in provenance if the private display name needs truncating to 100 characters.

Nutrition provenance is independent of allergen verification. Every imported food
starts with incomplete/unknown allergen metadata. A private copy also starts unknown;
copying USDA nutrition never asserts that the food is allergen-free. Known declared
source allergens and ingredient labels are retained in private copies, with review
reset to incomplete. When an ingredient is saved, any declared source allergens are
retained even if its form omits them; the ingredient review may add declarations.

## Recipes and ingredient review

Create a private recipe with 1–20 full-batch servings and one or more suitable meal
slots. Add up to 30 weighed ingredients from either food collection. Saving an
ingredient snapshots its current source name, rates and provenance. Source food
edits do not refresh a recipe automatically: edit/reselect the ingredient to refresh
it, reviewing its metadata again. Up to 100 recipes per account are supported.

Recipe allergen declarations, each ingredient's allergen declarations/review, dietary
tags and unwanted-ingredient labels are separate fields. Recipe and ingredient review
checkboxes must both be complete when any allergy restriction applies. A matching
declared allergen or an incomplete review excludes the recipe. Declared allergens
are checked even when another metadata field is incomplete.

Supported allergy labels: milk, egg, peanut, tree nuts, soy, gluten, fish, crustaceans,
molluscs, sesame, celery, mustard, lupin and sulphites. Comma, semicolon or newline
separates labels. Aliases include dairy/milk, eggs/egg, peanuts/peanut, soya/soy and
sulfites/sulphites. Broad nuts excludes both tree nuts and peanut; shellfish excludes
both crustaceans and molluscs. Other allergy notes remain saved but block automatic
planning until clarified. This is explicit label matching, not medical interpretation.

Dietary preferences supported by the planner are vegetarian, vegan and pescatarian,
matched to separately declared recipe tags. All requested tags must be present;
no dietary tags are inferred from a name or nutrient values. Unsupported free text
blocks automatic planning instead of being silently ignored. Manual diary use stays
available. Unwanted ingredients use comma-separated ingredient labels, compared
exactly ignoring case/extra spaces. Include all constituents of compound foods;
use consistent names for restrictions and labels. Missing ingredient labels exclude
a recipe when unwanted-ingredient restrictions apply. Prose such as “avoid olives”
must be clarified to “olives”. No fuzzy or semantic synonym matching is claimed.

Ingredient declarations and review do not establish cross-contact safety. Recorded
preferences and source nutrition are not a guarantee that a food is safe.

## Draft, review, save, then log

Choose a date using your saved IANA timezone (default Europe/Sofia). Set all four
manual calorie/macro targets first; explicit zero is valid, unset is not inferred.
The planner chooses breakfast, lunch, dinner and snack recipes without relaxing
exclusions. Recipes may repeat across suitable slots.

The finite grid uses 0.25–2 servings in 0.25 steps, up to the first 20 eligible
recipes per slot ordered by name/ID, and 128 retained combinations after each search
stage. A normalized squared difference scores calorie and all three macro targets
with equal weight; intermediate stages compare progressive fractions 25%, 60%,
90%, 100% of the daily targets. This is a bounded heuristic, not a global optimizer.
Search limits and exclusions appear explicitly. With no eligible option for a slot,
no draft is saved and the reason is shown. Out-of-range targets are identified;
otherwise a closest-found draft can still miss targets. Only exact equality of all
four totals is described as matching targets. No tolerance silently changes targets.

Review the target, planned amount and explicit over/below differences. Adjust a
portion or swap to another eligible recipe before saving. Saving a plan with any
difference requires explicit acceptance. Saved plans are read-only and retain their
target, restriction, recipe, ingredient, quantity, nutrient and provenance snapshots.
Current and original restrictions are both checked on edits, save and Log meal.
Changing a source recipe/food or deleting a recipe never rewrites saved plans.

Planning and saving do not touch the diary. An explicit confirmed **Log meal** POST
writes one diary snapshot per ingredient on the plan's displayed date. The plan row
is locked for all mutations. A permanent logged-at marker plus unique ingredient
positions prevents double-click and concurrent duplicate logging. Deleting a logged
diary entry does not clear that marker; correct existing entries or add a deliberate
manual entry rather than accidentally logging the same plan meal again. All
ingredient inserts and the marker commit together or roll back together.

## Decimal arithmetic, dates and bounds

Private manual rates use at most two decimals. Imported/shared rates and editable
USDA copies retain up to six decimals, stored as numeric and calculated with Decimal.
Grams and targets use two decimals. For recipes, batch grams × portion / batch servings
is rounded once to 0.01 g with ROUND_HALF_UP; portions producing an ingredient below
0.01 g or above 10000 g are rejected. Planned and logged grams are identical.
Nutrient contributions use rate × grams / 100 without intermediate rounding. Daily
sums are exact; only display rounds to whole numbers using ROUND_HALF_UP (halves away from zero). The same formatter covers calories, nutrient grams, portion grams, targets and remaining/over-target amounts in Nutrition and the dashboard. Edit forms retain stored precision. Unknown values, incomplete known subtotals and unset targets keep their explicit labels. Exact positive overages retain their over-target status even when the displayed amount rounds to 0.

| Field | Input bound |
| --- | --- |
| Private food / recipe name | 1–100 characters |
| Shared preparation description / sourced diary name | 1–300 characters |
| Calories per 100 g | 0–1000 kcal or blank for unknown |
| Each macro per 100 g | 0–100 g or blank for unknown |
| Consumed / batch ingredient grams | 0.01–10000 g |
| Daily calories | 0–20000 kcal or blank for unset |
| Daily macro target | 0–2000 g or blank for unset |
| Allergy / preference notes | Up to 2000 characters per field |
| Ingredient labels | Up to 30 labels, 1000 total characters |
| Diary / plan date | 1900-01-01 through 2100-12-31 |

These are data-entry limits, not dietary recommendations. Reject nonfinite/negative
numbers, scientific notation and excess precision rather than silently rounding inputs.
NULL remains unknown, distinct from explicit zero. The manual diary shows known
subtotals and marks affected nutrient totals and remaining amounts incomplete. If a
known subtotal is already above target, it shows “At least … over target”. An empty
day has a known empty sum of zero. Unknown nutrients exclude automatic recipe planning.

The diary compares entries to current manual targets; saved meal plans keep their
original targets. The settings/diary 4/4/9 explanation displays calorie/macro target
differences without changing them. “Today” follows the saved IANA timezone, including
DST. Changing timezone never moves existing dated entries. Diary snapshot edits change
only that entry, retain source attribution and mark sourced values as manually edited.
All reads/writes enforce ownership and parent IDs. SQL is parameterized, mutations
transactional and POST+CSRF protected, with deletion confirmations.

## Validation and manual checklist

Run the full PostgreSQL suite in disposable schemas within the existing database:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

Coverage includes Decimal/partial values, bounded search and infeasible targets,
allergen/ingredient/diet exclusions, unsupported metadata, recipe/diary CRUD,
shared/private privacy, forged parent IDs, admin permissions, CSRF, snapshots after
source edits/deactivation/deletion, concurrent duplicate logging, rollback, source
import precision/unknowns and repeatable migrations/seeding.

Executed for this update: **118 tests passed, zero skipped**, against PostgreSQL in
Docker. The official USDA ZIP rebuilt the bundled JSON byte-for-byte. Migrations
004/005/006 applied to the existing database; a repeated upgrade reported up to date.
Fingerprints of all 13 pre-existing data tables matched before/after, including
private foods and the existing diary entry (new columns excluded and decimal scale
normalized). The ledger gained only the three intended versions. Deployment, secret
and dependency file hashes matched. Both web replicas are healthy; nginx config,
HTTPS 200, HTTP 308 redirect and health responses from both replicas passed.

An isolated browser account exercised registration, settings, library copy, recipe
ingredient selection, four-meal generation, portion adjustment, saving and explicit
logging. Plan/diary pages had no horizontal overflow at 375px or 1280px. The temporary
account and its records were removed and existing-data fingerprints rechecked.
Administrator restrictions and concurrent duplicate logging were tested against
PostgreSQL; no existing account's permissions were changed for browser testing.

1. Search **All foods** for rice; compare raw and cooked entries, then milk fat
   percentages. Check the source/version link and per-100-g edible preparation.
2. Copy a library food into My foods and edit it. A second account must not see the
   copy. As an administrator, edit/deactivate a shared food; ordinary users must be
   denied those routes and the inactive source must leave new food choices.
3. Build recipes for all four slots using saved foods, full-batch grams and servings.
   Leave a nutrient unknown and verify incomplete recipe nutrition and exclusion.
4. Set allergy labels, ingredient dislikes and dietary tags separately. Missing recipe
   or ingredient reviews and declared conflicts must be explained, with no fallback
   that relaxes restrictions. Read the cross-contact limitation.
5. Generate a draft with deliberately infeasible targets. Check explicit differences
   and search limits. Adjust portions, swap an eligible meal and accept differences
   to save. Verify the diary is still unchanged.
6. Log one consumed meal explicitly. Repeat/double-click and confirm that no duplicate
   entries appear. Edit/delete a diary entry and verify totals and confirmation.
7. Edit source food/recipe values or delete the recipe: saved plan and past diary
   snapshots must retain their original names, rates, provenance and quantities.
8. Verify timezone-local Today, date navigation, empty states and a narrow/mobile
   layout. With a second account, copied recipe/plan/ingredient IDs must return 404.
