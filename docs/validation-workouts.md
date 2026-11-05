# Workout editor validation — 2026-11-04

This is the original editor preparation record. The current 36-test suite has since
run against native PostgreSQL 18 in Docker; see [deployment validation](./validation-deployment.md).

17 checks passed: 10 foundation regression tests and 7 new PostgreSQL-backed integration tests.
The integration suite ran against the PGlite embedded PostgreSQL engine through a test-only stdio adapter for the Python cursor API. It exercised actual SQL, database constraints and transactions, and Flask requests/templates. The app still uses normal psycopg connections in deployment; no PGlite dependency or adapter is included in the app.

Coverage: create/rename plans and days; exercise add/edit/remove; rep/sets validation with no partial writes; SQL parameterisation and HTML escaping; all private GET/POST routes against a second member; mismatched parent/child IDs; deleting contents and appending positions; confirmation/CSRF; retaining historical sessions and sets after plan deletion.

Not verified here: Docker rebuild/start, native psycopg wire protocol against PostgreSQL 18, Windows update script execution, browser interaction, or concurrent requests from multiple database connections. The PostgreSQL server could not open sockets in this workspace; the embedded engine was used instead. Complete the manual checklist in README-WORKOUT-UPDATE.md after installing on the laptop.

No schema changes or dependencies were added. Account data, .env, Compose project name and volume names are unchanged. The patch ZIP excludes database initialization files and compose.yaml.

To rerun native integration tests: provide FITHUB_TEST_DATABASE_URL for a disposable PostgreSQL database, use Python 3.12 with the pinned application dependencies, and run python -m unittest discover -s tests -v from the complete project root. Tests use a unique schema and drop only that schema after the suite. No pgserver or PGlite dependency is needed for native tests.
