# Recipe collection expansion

Implemented on `ui/purple-theme`. The live PostgreSQL count was inspected before
changes: **40 total, 40 active**. After import: **132 retained rows, 100 active**;
FitHub Chroma `fithub_recipes_v1` contains **100 documents**. Ninety-two source IDs
were added, eight existing IDs updated, and 32 pilot recipes retired because they
contain unspecified ingredient quantities. No recipe rows, plans, plan references,
or diary snapshots were deleted. A second import reports **0 new, 0 changed**.
Administrator deactivation is preserved; import never reactivates a recipe.

## Source and validation

UniTools, commit `2f554073f7e730b18d707fe1164d18308f6ec16c`, remains pinned.
JSON SHA256: `41fdc0572b7272ca557d2df22a0fac1cc1f18146e97ebd2c8258c395daf87d8a`.
Recipe data: **UniTools — theunitools.com, CC BY-SA 4.0**. Full data, adaptations,
baseline snapshots, [attribution](../db/data/unitools/ATTRIBUTION.md) and
[license](../db/data/unitools/LICENSE) stay together.

Of 501 source recipes, **122 pass validation**, **100 are selected**, **379 are
rejected**, and **22 valid recipes are outside the priority selection**. Detailed
per-recipe reasons are in [validation-report.json](../db/data/unitools/validation-report.json).
No missing salt, oil or other “to taste” amount is invented. Accepted ingredients
have an explicit identity mapping, a finite positive quantity and a supported
unit: g, kg, ml, l, piece, clove, tbsp, tsp, pinch, sprig or slice. Scaling rules
are validated. All four nutrients must be present, finite and nonnegative
(at most 10,000 per source serving); servings must be integers 1–100. English
preparation instructions and nonnegative integer prep/cook minutes are required.
Numbers are stored as Decimal strings without source-value rounding.

Selection prioritizes recorded low-fat protein, breakfast/main/soup/snack category,
vegetarian tags, recorded quick preparation, higher protein, then lower fat, with
stable source-slug ties. See the explicit [selection](../db/data/unitools/manifest.json).
There are only **one low-fat protein recipe and seven breakfasts** among the
selected measured recipes. Increasing collection size cannot supply missing
source measurements or create new lean recipes. Source categories: 25 mains,
5 soups, 7 breakfasts, 9 snacks, 18 breads, 32 desserts, 2 sauces, 1 side, 1 drink.
Bread/dessert/sauce/drink recipes are snack options, never relabelled main meals.

| Coverage (overlapping labels) | Previous pilot | Expanded measured collection |
|---|---:|---:|
| Recipes | 40 | 100 |
| Breakfasts | 8 | 7 |
| Low-fat protein: protein ≥20 g, fat ≤10 g | 1 | 1 |
| Vegetarian source tags | 12 | 70 |
| Quick: recorded prep + cook ≤30 min | 14 | 10 |
| Snack slot | 6 | 62 |
| Meal prep: source batch ≥4 servings | 36 | 97 |
| Fully reviewed allergen declarations | 4 | 1 |

Ingredient identity is distinct from allergen verification. New unresolved mappings
explicitly use `reviewed: false`; 579 selected ingredient occurrences remain
incomplete. Active allergies fail closed before retrieval. In particular, both
collections yield **no eligible shared recipes for the milk-allergy benchmark**.
Exclusions and dietary preferences are not relaxed to populate a full day.

## Retrieval and exact fitting

Every eligible PostgreSQL recipe is considered before per-slot selection. Chroma
receives only eligible public IDs, separately for each meal slot; no user data is
indexed. Each slot contributes up to three lexical/target-fit candidates, with
**at most 12 distinct model recipes overall**. Previously a global lexical result
could crowd out entire slots; the fallback could exceed 12. Unknown/stale Chroma
IDs remain untrusted. Index updates remove only manifest-listed retired IDs from
the FitHub-owned collection, not their PostgreSQL history.

The server fits against the **full eligible collection**, not just model-selected
IDs. It ranks candidates using exact normalized per-slot target shortfall before
the unchanged **20 recipes per slot / 128 retained states** search limits. Coach
preferences break equal-score ties. Portion bounds remain **0.25–2 servings in
0.25 increments**. Decimal upper limits, unknown-nutrient checks, optional snacks
only on strict fit improvement, preview confirmation, current exclusions and
save-time target/version checks remain in force. No schema or dependency change.
The bounded search does not claim a global optimum.

## Matched comparison

Same fitting implementation, preference text, source starter snack pool and target
settings were used for both collections. Model output was **mocked** with all
optional slot preferences null, isolating server selection from model variability.
PostgreSQL and Chroma were real. The previous public index documents were briefly
restored for comparison, then the final 100-document index was reapplied.
Measured times include retrieval and fitting, exclude model/network inference.
Raw selections, exact totals, normalized scores and notes:
[recipe-expansion-benchmark.json](recipe-expansion-benchmark.json).

Remaining amounts below are exact calculation results; trailing zeroes omitted
only for readability. Order is **kcal / protein g / carbs g / fat g**.

| Targets | Previous remaining | Expanded remaining | Previous / expanded seconds |
|---|---|---|---:|
| 2000 / 140 / 230 / 65 | 5.9 / 5.5165 / 2.684 / 1.978 | 0 / 17.5 / 2.5 / 0.25 | 0.2887 / 0.2845 |
| 1800 / 150 / 180 / 50 | 180.9 / 29.5165 / 2.184 / 0.978 | 255 / 48.5 / 8.5 / 0.5 | 0.1839 / 0.1768 |
| Vegetarian: 2000 / 120 / 250 / 60 | 120.3 / 22.0055 / 2.728 / 0.826 | 140 / 25 / 12 / 0 | 0.0574 / 0.1163 |
| Milk allergy: 2000 / 140 / 230 / 65 | Blocked by eligibility | Blocked by eligibility | <0.0001 / <0.0001 |
| All targets unset | No imposed limits; available main meals only, no snack | Same rule | 0.0185 / 0.0241 |

All generated totals satisfy every configured upper limit. **Collection expansion
does not improve overall normalized fit in these three configured examples**:
retiring useful but unquantified pilot meals narrows lean-protein combinations.
For balanced targets the normalized squared shortfall changes 0.002624 → 0.015758;
lean 0.049351 → 0.126944; vegetarian 0.037555 → 0.050607. Targets remain unchanged.
The original pilot is a historical comparison, not a safe reimport recommendation.

## Verification

- Focused recipe suite: **13 passed**, 6.333 seconds.
- Full Docker/Python 3.12/PostgreSQL suite: **271 passed, zero skipped**, 93.798 seconds.
- Coverage includes repeat imports, inactive preservation, source/license checksums,
  invalid units/nonfinite nutrients/missing quantities, existing plan and diary
  snapshots, ownership, active exclusions, newly added full-pool candidates,
  shortlist bounds, exact fractional caps, snacks, swaps, stale targets and saves,
  fabricated model output and provider failure. Workout Review tests also pass.
- Real Chroma retrieval for “miso soup wakame tofu dashi quick meal” returns newly
  imported `unitools:miso-soup`, alongside other added IDs; shortlist 9, eligible 100.
- Both FitHub replicas were rebuilt and report healthy; PostgreSQL and Chroma healthy.
- **Live `fithub-coach` attempt did not succeed**. The existing configured provider
  is unreachable from FitHub (`ConnectError`, including authenticated `/api/models`
  discovery). No API key or provider response body is printed. The two synthetic
  comparison attempts are recorded separately in
  [recipe-expansion-live.json](recipe-expansion-live.json). No draft or diary was
  created. The other AI project's configuration and containers were not modified.

## PowerShell commands

Run from the repository. Existing volumes and account data are retained.

```powershell
Set-Location E:\FitHub-School
New-Item -ItemType Directory -Force tmp | Out-Null
docker compose up -d --wait --wait-timeout 120 db chroma
docker compose build web

# Validate without database/index writes; save the detailed rejection report.
docker compose run --rm --no-deps -T --volume "${PWD}/tmp:/reports" web flask --app app import-recipes --dry-run --report /reports/recipe-validation.json

# Repeatable stable-ID import and FitHub-only index synchronization.
docker compose run --rm --no-deps -T web flask --app app import-recipes --index
docker compose run --rm --no-deps -T web flask --app app import-recipes --dry-run

# No new migration is required; apply any outstanding existing migrations safely.
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose up -d --no-deps --wait --wait-timeout 180 web
docker compose up -d --no-deps --wait --wait-timeout 120 nginx

docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v

# Matched synthetic comparison; mock model, real database and Chroma.
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" web python scripts/benchmark_recipes.py --baseline /srv/db/data/unitools/pilot-v1-snapshots.json

# Separate live checks after Open WebUI is reachable; credentials remain server-side.
docker compose exec -T web flask --app app nutrition-models
docker compose exec -T web flask --app app nutrition-smoke --kcal 2000 --protein 140 --carbs 230 --fat 65
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" web python scripts/benchmark_recipes.py --baseline /srv/db/data/unitools/pilot-v1-snapshots.json --live
```

The checksum-verified pinned upstream download remains available as
`./scripts/download-unitools.ps1`; no new upstream version is fetched by imports.
