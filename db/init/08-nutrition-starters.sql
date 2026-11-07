BEGIN;
ALTER TABLE meal_plan ADD COLUMN IF NOT EXISTS include_starters boolean NOT NULL DEFAULT false;
INSERT INTO schema_migration(version) VALUES ('008_nutrition_starters') ON CONFLICT DO NOTHING;
COMMIT;
