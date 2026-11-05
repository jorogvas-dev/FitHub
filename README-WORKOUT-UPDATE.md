# FitHub v0.2 — workout plan editor

## Apply to your existing installation

This is the historical editor update guide. Current installations should use the
[phase 1 migration and rebuild instructions](./docs/phase1-ui-onboarding.md).

This is an update ZIP, not a complete standalone starter. It contains FitHub-School/app, tests, documentation, and scripts/update-workouts.ps1.

1. Optional source backup before extracting: copy your existing app folder elsewhere.
2. Extract the ZIP directly into E:\ and allow replacement of matching files inside E:\FitHub-School.
   The existing compose.yaml, .env and database initialization files are not in this update ZIP and stay untouched.
3. In PowerShell:

```powershell
cd E:\FitHub-School
powershell -ExecutionPolicy Bypass -File .\scripts\update-workouts.ps1
```

No database reset, new volumes or additional dependencies are needed. Existing accounts and data are preserved.
Open http://localhost:8010/plans after logging in. If needed, refresh the browser to load the updated stylesheet.

## Available

- Create, list, rename and delete your own workout plans.
- Add, rename and delete training days.
- Add exercises from the library; configure sets and min/max reps; edit/remove prescriptions.
- Plan/day/exercise deletion requires a dedicated confirmation form and CSRF token.
- Other users cannot view or modify your plans, including by guessing URLs.
- Plan deletion removes planned days/prescriptions while existing workout sessions and set logs remain.
- Exercises and days append in creation order. Position numbers may have gaps after deletion; display stays sequential.

Workout logging, fitness profiles, bodyweight tracking and administrator exercise management are available. See [phase 1](./docs/phase1-ui-onboarding.md) for the current migration commands, onboarding and timed-exercise limitations. Reordering and AI coaching remain outside the implemented scope.

## Manual check

Create a plan named '4-day strength'. Add 'Upper A', add Pull-up for 4 sets of 6–10 reps. Edit to 5 sets. Rename the day. Verify the dashboard plan count. Log out and register a second account: its plan list should be empty and the first user's plan URL should return 404. Return to the first account and delete the plan using the confirmation form.
