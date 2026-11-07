# Nutrition workspace presentation

For the current Food Library sorting, whole-number display, theme and verification results, see [the purple update](./food-library-purple-update.md). The implementation notes below describe the preceding navigation update.

The daily diary is the Nutrition landing page at https://localhost:8443/nutrition.
It reuses the dashboard's plum/purple workspace, grouped sidebar, inline SVG
icons and native keyboard-accessible mobile menu. The six nutrition links expose
Daily diary, Food library, My foods, Recipes, Meal plans, and Preferences & targets.

The primary **Log food** action opens both food collections for the selected diary
date. Collection navigation, search, log forms, back/cancel links and the existing
successful logging redirect retain that date. Meal-specific manual entry links
retain their meal and date. Copying/editing a saved food remains its existing separate
management flow.

Responsive cards replace the diary, food-selection and plan-difference tables.
Consumed amounts, daily/captured targets and remaining/over-target balances have
separate labels. Unknown nutrients remain unknown, incomplete consumed values are
labelled known subtotals, and incomplete overages retain their lower-bound wording.
Unset targets, explicit zero, empty meals, calorie/macro discrepancies, snapshot
source details, USDA preparation/edible-portion information and allergy notices remain.
Planning still requires separate confirmed POST actions to log consumed meals.

This update changes templates, a scoped stylesheet, navigation and tests only.
Application Python, permissions, routes, Decimal calculations, schema, dependencies,
environment/secrets and deployment settings were not changed. No migration is needed.

## Rebuild in PowerShell

With the existing stack running:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose up -d --no-deps --wait --wait-timeout 180 web
```

## Executed verification

**132 tests passed; zero skipped**, against Docker/PostgreSQL:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

Four new PostgreSQL integration tests follow the primary diary action, collection
links, private/shared logging, back/cancel links, selected-date redirects, unknown
nutrients, attribution and read-only empty-state visits. Existing suites cover
calculations, ownership, CSRF, snapshots, planner restrictions and duplicate logging.
The initial run caught a shared-template collision with the training-day context;
nutrition date handling is now scoped to nutrition blueprints. The full suite was
rerun successfully after the fix and again after the final presentation adjustments.
`git diff --check` passed.

Live HTTPS checks used isolated Microsoft Edge contexts through the browser protocol
and the existing temporary Node runtime; no automation dependency was added. Twenty
screen/viewport cases passed at **1280px and 375px**:

- Member: diary with incomplete nutrients and an over-target lower bound, shared food
  selection, My foods, logging form, meal-plan list, saved details and draft generation.
- Admin: empty diary with unset targets, empty private foods and empty meal plans.
- All pages had no horizontal overflow or off-screen form controls. Admin navigation
  stayed role-dependent. Mobile Menu opened with Enter and Tab reached a visibly
  focused Dashboard link. Collection/date links and an actual POST+CSRF log kept the
  selected past date, rather than reverting to today.
- Provenance, unknown allergen metadata, cross-contact notices, plan differences and
  required meal-logging confirmation forms remained present.

Both web replicas were rebuilt and became healthy; the existing proxy and other
services remained healthy. Temporary QA accounts and their records were removed in
cleanup. All **19 public table row-count/content fingerprints** matched before/after;
all **40 protected application Python/schema/deployment/environment file hashes**
matched. Existing personal data does not appear in these screenshots. Screenshots
show actual stored records of temporary QA accounts, not a static prototype.

| Screen | 1280px | 375px |
|---|---|---|
| Diary, incomplete data | [Desktop](./screenshots/nutrition/diary-1280.png) | [Mobile](./screenshots/nutrition/diary-375.png) |
| Food selection | [Desktop](./screenshots/nutrition/food-selection-1280.png) | [Mobile](./screenshots/nutrition/food-selection-375.png) |
| Log food | [Desktop](./screenshots/nutrition/log-food-1280.png) | [Mobile](./screenshots/nutrition/log-food-375.png) |
| Meal plans | [Desktop](./screenshots/nutrition/meal-plans-1280.png) | [Mobile](./screenshots/nutrition/meal-plans-375.png) |
| Saved plan details | [Desktop](./screenshots/nutrition/meal-plan-detail-1280.png) | [Mobile](./screenshots/nutrition/meal-plan-detail-375.png) |
| Empty diary, no targets | [Desktop](./screenshots/nutrition/empty-diary-1280.png) | [Mobile](./screenshots/nutrition/empty-diary-375.png) |

Other browser engines and the live deployment failover checker were not rerun for
this presentation-only update.
