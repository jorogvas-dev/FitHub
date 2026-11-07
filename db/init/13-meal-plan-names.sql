BEGIN;
ALTER TABLE meal_plan ADD COLUMN IF NOT EXISTS name varchar(100)
 CHECK(name IS NULL OR char_length(trim(name)) BETWEEN 1 AND 100);
INSERT INTO schema_migration(version) VALUES ('013_meal_plan_names') ON CONFLICT DO NOTHING;
COMMIT;
