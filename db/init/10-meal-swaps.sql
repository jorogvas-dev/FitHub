BEGIN;
ALTER TABLE meal_plan_meal ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 1 CHECK(version>0);
ALTER TABLE recipe_catalogue ADD COLUMN IF NOT EXISTS prep_minutes integer CHECK(prep_minutes>=0);
ALTER TABLE recipe_catalogue ADD COLUMN IF NOT EXISTS cook_minutes integer CHECK(cook_minutes>=0);
INSERT INTO schema_migration(version) VALUES ('010_meal_swaps') ON CONFLICT DO NOTHING;
COMMIT;
