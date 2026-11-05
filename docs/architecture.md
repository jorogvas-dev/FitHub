# FitHub architecture

Manual nutrition tracking adds private settings, saved foods and date-scoped diary
snapshots through migration `003_nutrition`. Calculations use Decimal; unknown values
remain NULL and make the affected total incomplete. IANA timezone rules define the
diary's Today. See [the nutrition guide](./nutrition.md) for validation and ownership
rules. The existing deployment, fitness records and workout snapshots are retained.

Current: browser -> nginx (local HTTPS) -> two identical Flask/Gunicorn replicas -> shared PostgreSQL. HTTP redirects to HTTPS. ChromaDB runs separately and is checked by /health, awaiting fitness knowledge and the AI coach adapter. Dozzle shows logs for containers labelled with the fithub-school Compose project.
Database ownership is enforced in dashboard and CRUD queries using the authenticated user identity; owner IDs supplied by the browser are ignored.

Tables: app_user, fitness_profile, exercise, workout_plan, workout_day, plan_exercise, workout_session, set_log, body_measurement, coach_message.
Relationships: user -> profile, plans, sessions, measurements, coach messages; plan -> days -> exercise prescriptions; session -> logged sets -> exercise.
A user's access to a linked workout_day is validated against plan ownership when a session is started. Set mutations lock the session row, and finished sessions are read-only. Profile and measurement routes also enforce ownership.

Future AI: existing AI project remains a separate service, inspected before integration. Use a fitness-only Chroma collection. Retrieve training history from PostgreSQL after authorization. No model is loaded in web workers.

This version follows the agreed personal fitness app scope: workout planning, logs, progress, AI coach. Gym memberships and class reservations from the initial optional proposal are not part of this version.

See [the deployment guide](./deployment.md) for the architecture diagram, pinned nginx/Dozzle versions, shared-secret session behavior, Docker DNS upstream recovery, persistent volumes, startup dependencies and repeatable validation commands. Both web replicas have no published host ports; nginx alone publishes localhost HTTP/HTTPS.

## v0.2 workout editor

The current UI and onboarding phase is documented in [phase 1](./phase1-ui-onboarding.md).
Exercise lifecycle/type columns and the expanded catalogue are installed through
explicit versioned migrations. Fresh SQL records the same migration versions.
The admin blueprint checks the database-backed administrator flag on every route;
only an explicit CLI command promotes an existing user. Setup fills missing profile
fields and inserts an owner-scoped measurement without replacing existing values.
Timed movements are reference-only until duration targets/logging are implemented.

app/routes/plans.py provides authenticated plan/day/exercise CRUD. Parent plan ownership is checked for every read and write; child IDs are checked against their parent. Mutation transactions lock the parent workout_plan row, serializing append positions and preventing overlapping edits/deletions. All SQL values use parameters. Deletion is POST-only with CSRF and an explicit confirmation form. Existing session/set history survives plan deletion through the existing FK SET NULL rules.

Shared foods use food_catalogue with stable USDA source IDs (migration 004), keeping
private food ownership keys intact. Recipe ingredients copy nutrition/provenance;
meal plans copy recipe and ingredient snapshots again (migration 005). Planning
never inserts diary rows. Explicit Log meal locks its owning plan, inserts ingredient
snapshots and sets a permanent duplicate-protection marker in one transaction.
See [nutrition architecture and operator commands](./nutrition.md).
