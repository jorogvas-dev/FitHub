ALTER TABLE plan_exercise ADD COLUMN IF NOT EXISTS rest_seconds integer
 CHECK(rest_seconds BETWEEN 1 AND 3600);
CREATE TABLE IF NOT EXISTS workout_schedule (
 schedule_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 user_id bigint NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
 scheduled_on date NOT NULL CHECK(scheduled_on BETWEEN DATE '1900-01-01' AND DATE '2100-12-31'),
 day_id bigint REFERENCES workout_day(day_id) ON DELETE SET NULL,
 prescription jsonb NOT NULL CHECK(jsonb_typeof(prescription)='object'),
 session_id bigint UNIQUE REFERENCES workout_session(session_id),
 cancelled_at timestamptz,
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 request_key text NOT NULL,
 UNIQUE(user_id,request_key),
 CHECK(session_id IS NULL OR cancelled_at IS NULL)
);
CREATE INDEX IF NOT EXISTS workout_schedule_user_date_idx ON workout_schedule(user_id,scheduled_on,schedule_id);
