BEGIN;
CREATE TABLE IF NOT EXISTS training_preferences (
 user_id bigint PRIMARY KEY REFERENCES app_user(user_id) ON DELETE CASCADE,
 days_per_week smallint CHECK(days_per_week BETWEEN 1 AND 7),
 minutes_per_session smallint CHECK(minutes_per_session BETWEEN 15 AND 180),
 equipment text[] NOT NULL DEFAULT '{}',
 avoid_movements text[] NOT NULL DEFAULT '{}',
 status text NOT NULL CHECK(status IN ('completed','skipped')),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(status <> 'completed' OR (days_per_week IS NOT NULL AND minutes_per_session IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS workout_draft (
 draft_id uuid PRIMARY KEY,
 user_id bigint NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
 document jsonb NOT NULL,
 preferences jsonb NOT NULL,
 candidate_ids bigint[] NOT NULL,
 revision integer NOT NULL DEFAULT 1 CHECK(revision > 0),
 created_at timestamptz NOT NULL DEFAULT now(),
 saved_at timestamptz,
 saved_plan_id bigint REFERENCES workout_plan(plan_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS workout_draft_user_idx ON workout_draft(user_id,created_at DESC);

INSERT INTO schema_migration(version) VALUES ('007_workout_drafts') ON CONFLICT DO NOTHING;
COMMIT;
