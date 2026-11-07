# Food Library, whole-number nutrition and purple theme

Verified on 6 November 2026 against the local Docker/PostgreSQL deployment.

## Result

- Member food selection, library logging and copied-food editing omit source badges, citations and external source links. Preparation, edible-portion instructions and allergen review status remain. Original food descriptions are preserved, including source names that are part of a food's name. Database provenance, diary snapshots and administrator source tools remain intact.
- Calories, protein, carbs and fat can each be sorted lowest or highest first per 100 g. Sorting uses stored numeric values; unknowns follow all known values in either direction. Ties use case-insensitive food name, then collection/ID, in the same order in both directions. Search, collection, preparation and selected diary date remain in the filter form; log actions retain the date.
- Nutrition and dashboard displays round to the nearest whole number with `ROUND_HALF_UP`: 2.49 → 2, 2.50 → 3 and 100.50 g → 101 g. Calculations and stored precision remain unchanged. Forms load exact saved values, including six-decimal imported rates. JSON meal-plan targets use the same display formatter.
- Unknown, incomplete known subtotal, unset target and explicit zero retain separate labels. Totals sum exact contributions before display rounding. Tiny positive overages retain their over-target label even when the amount displays as 0.
- Shared tokens supply near-black plum backgrounds, purple cards, lavender accents, muted purple primary actions, darker secondary actions and rose-plum destructive actions. Training status panels, notices, tables, public pages and browser theme color use the updated palette.

## Verification

The existing Docker/Python 3.12/PostgreSQL suite passed **141 tests, zero skipped**, in 46.448 seconds. New regression coverage checks all four nutrients in both directions, full-precision ordering despite identical rounded labels, numeric 2 versus 10, deterministic equal-name ties, explicit zero, unknowns last, private/shared filtering, invalid sort input, provenance retention and raw edit values. Existing snapshot, ownership, authentication, CSRF and calculation coverage also passed. Editor diagnostics reported no errors in changed Python modules and new tests.

A local headless Chrome/Playwright run passed **94 acceptance checks**, with **no JavaScript errors**. The integrated browser could not open a page, so checks ran against the actual HTTPS deployment using local Chrome. Two synthetic accounts were created for testing and removed afterwards; existing member records were not used for screenshots.

Browser checks exercised every nutrient sort in both directions, selected-date/filter submission and log links, zero versus unknown, incomplete zero subtotals, exact-sum rounding, half-up targets, grams and nutrient precision in edit forms, and dashboard formatting. All reviewed pages fit the viewport at **1280 px and 375 px**, without document-level horizontal overflow: Food Library, diary, dashboard, workout plans/history, exercise library, profile and meal plans. Screenshots were visually reviewed for palette, readable text, wrapping and action distinctions.

Computed text contrast across the main backgrounds, cards and selected navigation is at least **6.14:1**. Primary button text contrast is **7.72:1**; secondary and destructive button text also exceed **4.5:1**. Keyboard Tab navigation exposes a **3 px solid lavender focus outline**. These are targeted contrast/focus checks, not a full accessibility audit.

## Screenshots

All screenshots use synthetic test data. Full browser results are in [checks.json](./screenshots/purple/checks.json).

| View | 1280 px | 375 px |
| --- | --- | --- |
| Food Library, filtered and sorted | [Desktop](./screenshots/purple/food-library-1280.png) | [Mobile](./screenshots/purple/food-library-375.png) |
| Diary, rounding and incomplete zero | [Desktop](./screenshots/purple/diary-1280.png) | [Mobile](./screenshots/purple/diary-375.png) |
| Dashboard | [Desktop](./screenshots/purple/dashboard-1280.png) | [Mobile](./screenshots/purple/dashboard-375.png) |
| Workout plans | [Desktop](./screenshots/purple/training-1280.png) | [Mobile](./screenshots/purple/training-375.png) |
| Workout history | [Desktop](./screenshots/purple/workouts-1280.png) | [Mobile](./screenshots/purple/workouts-375.png) |
| Exercise library | [Desktop](./screenshots/purple/exercises-1280.png) | [Mobile](./screenshots/purple/exercises-375.png) |
| Profile | [Desktop](./screenshots/purple/profile-1280.png) | [Mobile](./screenshots/purple/profile-375.png) |
| Meal plans | [Desktop](./screenshots/purple/meal-plans-1280.png) | [Mobile](./screenshots/purple/meal-plans-375.png) |

Additional captures: [missing values sorted last](./screenshots/purple/sorting-missing-desc-1280.png), [logging](./screenshots/purple/log-food-375.png), [exact edit values](./screenshots/purple/edit-precision-375.png), [destructive action](./screenshots/purple/delete-1280.png), [keyboard focus](./screenshots/purple/keyboard-focus-1280.png).

## Rebuild from PowerShell

With the existing stack running:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose up -d --no-deps --wait --wait-timeout 180 web
docker compose ps
```

No migration or new application dependency is required. Both web replicas were rebuilt and restarted successfully. The AI coach remains future work.

To repeat the suite:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```
