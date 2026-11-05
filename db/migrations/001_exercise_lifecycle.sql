-- Additive migration for installations with the original fitness schema.
ALTER TABLE exercise ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
ALTER TABLE exercise ADD COLUMN IF NOT EXISTS tracking_type text NOT NULL DEFAULT 'reps'
 CHECK(tracking_type IN ('reps','duration'));
-- Recognise the original seeded hold without changing any existing numeric targets/logs.
UPDATE exercise SET tracking_type='duration'
 WHERE name='Plank' AND equipment='Bodyweight'
 AND description='Maintain a stable trunk while breathing normally.';
