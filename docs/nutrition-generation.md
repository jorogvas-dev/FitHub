# Optional nutrition drafts from onboarding

Verified locally on 2026-11-07.

Onboarding and training preferences now offer an optional **Generate nutrition plan** action. It remains available from Fitness profile and Meal plans. **Skip for now** returns to the dashboard without creating a draft. Existing profile and bodyweight setup stays independent of nutrition planning.

The generation form shows the account's saved dietary preferences, allergies, excluded foods and manually entered calorie/macro targets. Its settings link returns to generation after saving through a fixed internal destination. Recipes can also be reviewed from the Recipes page.

## Starter recipes and calculations

Eight shared, read-only starter templates are available without copying recipes into each account:

| Slot | Starter options |
| --- | --- |
| Breakfast | Banana oats; yogurt and berries |
| Lunch | Chickpea rice bowl; chicken rice bowl |
| Dinner | Lentils and sweet potato; tofu rice bowl |
| Snack | Apple and peanut butter; banana and berries |

Templates refer to 15 existing Food library ingredients by permanent USDA source IDs. The [starter recipe service](../app/services/starter_recipes.py) verifies each source identity and raw/cooked preparation against the bundled [USDA subset](../db/data/usda-sr-2018.json), then uses the active database ingredient's exact nutrient values. Recipe cards and plan snapshots show the ingredient quantities and preparation instructions. Provenance is retained in snapshots.

Unavailable/deactivated foods, changed descriptions/preparations, unexpected source allergen declarations and incomplete nutrients block affected templates with an explanation. Existing administrator edits are not overwritten. Starter declarations separately mark oats/gluten, yogurt/milk, tofu/soy and peanut butter/peanut. These ingredient labels follow the existing supported matching vocabulary, consistent with [Food Standards Agency allergen guidance](https://www.gov.uk/government/publications/allergen-labelling-for-food-manufacturers/allergen-labelling-for-food-manufacturers). They do not establish cross-contact safety or certify packaged substitutes.

New accounts have **Include FitHub starter recipes** selected by default. Accounts with private recipes can opt in; existing private-only workflows and old plans retain their recipe scope. The selected scope is captured in the draft so eligible swaps use the same recipe collections.

Generation uses FitHub's existing deterministic [meal planner](../app/services/meal_planner.py), including its Decimal arithmetic, recipe snapshots, exclusions, serving grid and bounded search. It does not send nutrition values or preferences to an inference service. Ingredient grams are rounded once to 0.01 g as before; nutrient calculations retain their precision and displays keep the shared whole-number rule.

## Optional targets and constraints

- With no targets, generation starts at one serving per meal where supported and displays all draft totals. No calorie/macro targets are calculated, inserted or changed.
- With some targets, only the values actually entered guide the search. Other targets remain unset, distinct from explicit zero. Differences are shown only for set targets.
- With all targets, the existing four-target search and explicit acceptance of differences continue to apply. Matching is described as exact only when the set targets match exactly; the search is not guaranteed to find a global optimum.
- A missing eligible meal slot prevents draft creation. The page identifies the blocked slot and lists recipe-specific allergy, ingredient, dietary or source-data reasons. Unknown restriction text requires clarification instead of being ignored.

Search bounds remain 0.25–2 servings in 0.25 steps, up to 20 eligible candidates per meal and 128 retained combinations per stage. Private candidates are ordered by name/ID, with eligible starter templates added afterwards. Restriction labels retain the existing comma-separated exact matching and documented aliases.

## Review, save and diary logging

Generation creates a private `meal_plan` with status `draft`. Reviewers can edit portions or swap to eligible meals using the existing meal editor, with totals recalculated from ingredient quantities. Starter snapshots contain stable `starter:` recipe keys; no account-owned recipe is created automatically.

Only an explicit POST **Save plan** changes status to `saved`. Target differences require acceptance when targets have been set; an all-unset draft can be saved after review without pretending targets were met. Saving does not write diary entries.

Only a separate confirmed **Log meal** POST adds consumed ingredients to the diary. Existing ownership checks, parent IDs, immutable saved snapshots, current/original restrictions, CSRF, row locks and duplicate-log protection are reused. New restrictions can block saving or logging an older snapshot. Forged swaps to excluded recipes are rejected server-side.

Additive migration [008_nutrition_starters](../db/migrations/008_nutrition_starters.sql) adds only the `meal_plan.include_starters` flag, defaulting to false for existing plans. Fresh installations use the matching eighth init script. Existing accounts, settings, foods, recipes, plans, bodyweight records and diary entries are not reset.

## Executed verification

**189 Docker/PostgreSQL regression tests passed**, zero skipped, in **61.191 seconds**. This includes all existing workout/inference, nutrition, ownership, rounding and migration tests. The focused existing meal tests passed (24 tests), and the nine new starter-flow integration tests passed.

New checks cover an account without recipes or targets, optional onboarding links, source values/preparation/provenance, review/portion swaps, explicit save and separate logging, milk/peanut/soy aliases, vegetarian/vegan exclusions, impossible breakfast restrictions, unknown labels, forged excluded swaps, inactive/changed/incomplete ingredient sources, partial/zero targets, changed allergies before save/log, privacy and repeatable migration preservation.

**32 browser checks passed** through the deployed HTTPS app at **1280 px and 375 px** using a temporary synthetic new account:

- The optional onboarding action and Skip for now worked; opening the generator created no draft.
- An all-unset-target draft displayed totals and four explicit Target not set labels. Swapping breakfast to yogurt and berries and changing to 0.75 servings recalculated totals; targets and diary remained untouched.
- Save plan changed the draft to saved while the diary stayed empty. Only a separate confirmed Log meal created the two consumed ingredient entries.
- Milk and peanut allergies, excluded soy and a vegetarian preference were honored in every selected snapshot. The form explained each excluded starter.
- Adding gluten excluded both remaining breakfast options; generation named the blocked breakfast slot, identified gluten/milk conflicts and inserted no draft.
- All eight screenshots fit without horizontal overflow. The temporary account and all its test records were removed.

Migration 008 was applied locally and both rebuilt web replicas are healthy. No additional dependencies or model setup were needed.

## PowerShell commands

Rebuild and apply the additive migration with the existing database running:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose up -d --no-deps --wait --wait-timeout 180 web
```

Run the regression suite in isolated PostgreSQL schemas:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

## Screenshots

All displayed data belongs to the synthetic test account.

| State | 1280 px | 375 px |
| --- | --- | --- |
| Optional onboarding action | [Desktop](./screenshots/nutrition-generation/onboarding-option-1280.png) | [Mobile](./screenshots/nutrition-generation/onboarding-option-375.png) |
| Unset targets and edited draft | [Desktop](./screenshots/nutrition-generation/unset-target-draft-1280.png) | [Mobile](./screenshots/nutrition-generation/unset-target-draft-375.png) |
| Allergy-filtered draft | [Desktop](./screenshots/nutrition-generation/allergy-filtered-draft-1280.png) | [Mobile](./screenshots/nutrition-generation/allergy-filtered-draft-375.png) |
| Blocked breakfast explanation | [Desktop](./screenshots/nutrition-generation/blocked-breakfast-1280.png) | [Mobile](./screenshots/nutrition-generation/blocked-breakfast-375.png) |
