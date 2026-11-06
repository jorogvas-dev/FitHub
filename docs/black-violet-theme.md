# Approved Black Violet theme

Implemented on 6 November 2026 after approval of the black background and gradient-button Superdesign revision.

[Design canvas](https://superdesign.dev/teams/86fde798-0f3d-4340-b34c-cba2f4717e39/projects/30611a05-742c-4650-9d2a-d546302d72c8) · [Dashboard preview](https://p.superdesign.dev/draft/f021307b-f912-47ce-b1f5-2efbfe56710b) · [Diary preview](https://p.superdesign.dev/draft/d21e163a-68e4-4fa9-9be1-6b012781c411)

## Behavior and implementation

The Dashboard groups planned training and recorded bodyweight in one column, with nutrition and the meal-plan state in the other. The diary uses four nutrient summaries, aligned food/nutrient/action columns on desktop, and stacked entry cards at 375 px. Both pages use the shared sidebar and native keyboard-accessible mobile menu.

The implementation uses Flask, Jinja and ordinary CSS. Every displayed record comes from the signed-in account and existing PostgreSQL queries. Canvas sample labels and fictional values are not embedded in the app. Start workout still requires selecting a plan and day; diary deletion still opens the existing confirmation page with checkbox, POST and CSRF protection. Manual targets, dates, snapshots, ownership checks and exact calculations retain their existing behavior. No schema migration, runtime JavaScript, frontend framework, external font or new application dependency was added.

The commit also includes the supporting Dashboard data/shell and Nutrition work already requested in this conversation: member Food Library citations are removed while database/admin provenance remains; all four per-100-g nutrient sorts use numeric values with unknowns last and deterministic ties; displays round half-up to whole numbers while storage, calculations and edit fields preserve precision. Incomplete known subtotals, entirely unknown values, unset targets and explicit zero remain distinct. Progress bars appear only for complete nutrients with positive targets.

## Palette

| Role | Hex |
| --- | --- |
| Background | `#000000` |
| Card / raised surface | `#131315` / `#1E1D22` |
| Border | `#37333D` |
| Main / secondary text | `#F5F3F8` / `#BDB7C7` |
| Accent / focus | `#D6C1F0` / `#DEC9FF` |
| Primary gradient | `#765599` → `#49315E` |
| Secondary gradient | `#332C3C` → `#1B181F` |
| Destructive gradient | `#7D445B` → `#492431` |
| Destructive label | `#F8D3DF` |
| Warning background / text | `#27221E` / `#E5C48C` |

Gradients run at 135 degrees. Controls have a 44 px minimum height and a visible 3 px lavender keyboard outline. Gradient-endpoint label contrast is at least **5.39:1**; checked normal text on the principal surfaces is at least **7.98:1**.

## Executed verification

- **141 Docker/PostgreSQL tests passed**, zero skipped, in the full working tree (43.181 seconds).
- **136 Docker/PostgreSQL tests passed**, zero skipped, against an exported snapshot of the staged application and tests (42.203 seconds). Five local training-presentation tests and their unrelated template changes were deliberately excluded from this commit.
- **112 Chrome/Playwright checks passed**, with no JavaScript errors. Checks covered both sorting directions for calories/protein/carbs/fat; numeric ordering, ties, unknowns and zero; retained filters/date; raw edit values; half-up totals/portions; current-user Dashboard records; real plan creation and exercise entry; existing deletion confirmation; desktop nutrient alignment; pure black background; gradients; focus; mobile navigation; and contrast.
- Dashboard and diary were checked at **1280 px and 375 px**, in populated and empty states. Both fit without document-level horizontal overflow. Screenshots were visually reviewed against the approved direction.
- Editor diagnostics reported no errors in the changed templates or theme stylesheets. The staged diff was reviewed, and the GitHub remote was verified as `https://github.com/jorogvas-dev/FitHub.git`.

Browser screenshots use a disposable QA account populated through the real registration, onboarding, bodyweight, plan/exercise and diary forms. Its records were removed after verification. Existing members' private data was not used for screenshots. No account credentials, environment files, local database state, private PDFs or temporary exports belong to the commit. Earlier unrelated training templates and local design artifacts remain outside it.

## Screenshots

| View | 1280 px | 375 px |
| --- | --- | --- |
| Populated Dashboard | [Desktop](./screenshots/black-violet/dashboard-1280.png) | [Mobile](./screenshots/black-violet/dashboard-375.png) |
| Populated diary | [Desktop](./screenshots/black-violet/diary-1280.png) | [Mobile](./screenshots/black-violet/diary-375.png) |
| Empty Dashboard | [Desktop](./screenshots/black-violet/empty-dashboard-1280.png) | [Mobile](./screenshots/black-violet/empty-dashboard-375.png) |
| Empty diary, unset targets | [Desktop](./screenshots/black-violet/empty-diary-1280.png) | [Mobile](./screenshots/black-violet/empty-diary-375.png) |
| Food Library | [Desktop](./screenshots/black-violet/food-library-1280.png) | [Mobile](./screenshots/black-violet/food-library-375.png) |

Additional checks: [destructive confirmation](./screenshots/black-violet/delete-1280.png), [keyboard focus](./screenshots/black-violet/keyboard-focus-1280.png), [mobile menu](./screenshots/black-violet/mobile-menu-375.png), [stored precision in an edit form](./screenshots/black-violet/edit-precision-375.png).

## PowerShell rebuild

With the existing database and other services running:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose up -d --no-deps --wait --wait-timeout 180 web
docker compose ps
```

The normal regression command remains:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```
