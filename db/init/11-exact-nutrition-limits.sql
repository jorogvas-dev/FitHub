BEGIN;
ALTER TABLE nutrition_diary_entry ALTER COLUMN grams TYPE numeric;
INSERT INTO schema_migration(version) VALUES ('011_exact_nutrition_limits') ON CONFLICT DO NOTHING;
COMMIT;
