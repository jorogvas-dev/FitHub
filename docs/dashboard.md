# Dashboard and shared navigation implementation

Implemented the approved [Superdesign dashboard](https://p.superdesign.dev/draft/f021307b-f912-47ce-b1f5-2efbfe56710b)
using Flask, Jinja, the existing stylesheet and a small member-workspace stylesheet.
There is no Tailwind runtime, external icon service, frontend framework or added
application dependency. SVG icons are inline and their Lucide/Feather licenses are
included in [LUCIDE-LICENSE.txt](../app/static/icons/LUCIDE-LICENSE.txt).

## Behavior

- Signed-in pages share grouped Dashboard, Training, Nutrition, Progress and Profile
  navigation. Administrator links are rendered separately, only for an administrator
  loaded from the database; existing server-side admin checks remain in force.
- Desktop navigation scrolls when necessary. Its separate administrator section and
  logout controls stay accessible. Below 900px a native details/summary menu replaces
  the sidebar. It expands in document flow, supports keyboard activation and does
  not overlay or trap focus. Text labels, focus outlines and the skip link remain.
- Start workout opens the existing workout plans page. The user must select a plan
  and training day before its existing POST+CSRF action creates a session. A dashboard
  visit never starts a workout. The latest plan's first available day is clearly
  labelled as a preview, not a calendar event or an automatic next workout.
- Log food opens the manual entry form for the user's timezone-local date. Add weight
  opens the existing bodyweight form. Their validation, ownership and write flows
  are unchanged.
- Dashboard reads are parameterized and scoped to the authenticated account, including
  training-day children and meal-plan links. Administrator status does not broaden
  access to another member's personal dashboard records.
- Counts are all-time counts. Training displays up to five actual prescriptions,
  their complete day count/set count, and a link to the remaining exercises. Existing
  inactive exercises remain visible, and legacy timed targets are never labelled reps.
- Nutrition reuses the existing settings, IANA date and Decimal daily calculations.
  Unknowns stay unknown, partial values are labelled known subtotals, remaining amounts
  stay unknown, and known overages show an explicit lower bound when necessary.
  Unset and explicit-zero targets remain distinct. Progress bars are shown only for
  complete macros with a positive target and are visually capped at the target.
- Latest weight and signed change use the two most recent recorded dates. Up to four
  actual measurements form the recent-record strip; it is not a forecast or goal.
- Today's owned saved meal plan (or draft) links to its actual details. Otherwise the
  empty state links to the existing planner. Planning does not log food automatically.
- Profile setup prompts and empty training, nutrition and weight states remain.
  All prototype names, dates, numbers, sample labels and placeholder action links
  have been replaced with real account values and existing routes.

## Rebuild in PowerShell

With the existing database, Chroma and nginx containers running:

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose up -d --no-deps --wait --wait-timeout 180 web
```

No migration is required. Schema, dependency files, database credentials, secrets,
Compose project/settings and persistent volumes are unchanged. Both web replicas
were rebuilt and became healthy. Open https://localhost:8443/dashboard after login.

## Executed verification

**128 tests passed, zero skipped**, using the existing Docker/PostgreSQL suite:

```powershell
docker compose run --rm --no-deps -T --volume "${PWD}:/srv:ro" --env FITHUB_TEST_DATABASE_URL=connect_timeout=3 web python -m unittest discover -s tests -v
```

The ten new dashboard integration tests exercise real PostgreSQL queries, private
records (including an admin attempting foreign-user parameters), actual training
previews, explicit plan/day POST selection, empty states, nullable nutrient values,
unset/zero targets, overages, final-only Decimal rounding, timezone date boundaries,
owned saved/draft meal plans, admin navigation, timed/inactive prescriptions and
read-only dashboard GETs. Existing tests cover profile/weight date changes, CSRF,
workout completion locks, historical snapshots, recipes and meal logging.

Live browser checks used isolated Microsoft Edge contexts through its browser
protocol with the temporary Node runtime; no automation dependency was added to
the project. Both roles signed in over the running HTTPS proxy. At 1280px and 375px:

- No horizontal overflow; correct desktop/sidebar or mobile/menu presentation.
- Admin controls absent for the member, visible and reachable for the admin. Direct
  admin requests returned 403 for the member and 200 for the admin.
- Mobile Menu opened with Enter, Tab reached Dashboard with a visible focus outline,
  subsequent Tab navigation reached Profile/Admin links, and Enter closed the menu.
- The existing plan/day selection and protected workout-start POST worked. The food
  form used the user's local date, and Add weight reached the existing weight form.
- Missing calories and fat displayed known subtotals, amber incomplete labels and
  unknown remaining amounts. Complete nutrients retained their calculated totals.

The screenshots below are full-page captures of the actual authenticated application,
not the design prototype. They use temporary QA accounts and their stored test data;
no existing personal account or health records appear in them. The member has complete
nutrition and no meal plan; the admin has incomplete nutrition and an owned saved plan.
Both accounts and all their records were removed after testing. All 19 existing public
table fingerprints matched before/after, and all 23 protected environment/schema/
deployment/dependency file hashes matched. No existing user's permissions changed.

| Account |1280px|375px|
|---|---|---|
| Member |[Dashboard](./screenshots/dashboard/dashboard-member-1280.png)|[Dashboard](./screenshots/dashboard/dashboard-member-375.png)|
| Admin, missing nutrition |[Dashboard](./screenshots/dashboard/dashboard-admin-1280.png)|[Dashboard](./screenshots/dashboard/dashboard-admin-375.png)|

Expanded mobile navigation: [member](./screenshots/dashboard/navigation-member-375.png)
and [admin](./screenshots/dashboard/navigation-admin-375.png).

The requested suite and four role/viewport browser cases were executed. There is no
separate linter/type-checker configuration in this repository. Deployment failover
scripts were not rerun for this UI update; deployment configuration was unchanged.
