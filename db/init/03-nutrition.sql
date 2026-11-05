BEGIN;
-- Additive manual nutrition tracking. No existing fitness records are changed.
CREATE TABLE nutrition_settings (
 user_id bigint PRIMARY KEY REFERENCES app_user(user_id) ON DELETE CASCADE,
 allergies text NOT NULL DEFAULT '' CHECK(char_length(allergies) <= 2000),
 unwanted_ingredients text NOT NULL DEFAULT '' CHECK(char_length(unwanted_ingredients) <= 2000),
 dietary_preferences text NOT NULL DEFAULT '' CHECK(char_length(dietary_preferences) <= 2000),
 timezone varchar(100) NOT NULL DEFAULT 'Europe/Sofia',
 target_kcal numeric(7,2) CHECK(target_kcal BETWEEN 0 AND 20000),
 target_protein numeric(6,2) CHECK(target_protein BETWEEN 0 AND 2000),
 target_carbs numeric(6,2) CHECK(target_carbs BETWEEN 0 AND 2000),
 target_fat numeric(6,2) CHECK(target_fat BETWEEN 0 AND 2000)
);
CREATE TABLE nutrition_food (
 food_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 user_id bigint NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
 name varchar(100) NOT NULL CHECK(char_length(trim(name)) > 0),
 kcal_100g numeric(6,2) CHECK(kcal_100g BETWEEN 0 AND 1000),
 protein_100g numeric(5,2) CHECK(protein_100g BETWEEN 0 AND 100),
 carbs_100g numeric(5,2) CHECK(carbs_100g BETWEEN 0 AND 100),
 fat_100g numeric(5,2) CHECK(fat_100g BETWEEN 0 AND 100),
 UNIQUE(food_id,user_id)
);
CREATE INDEX nutrition_food_user_idx ON nutrition_food(user_id);
CREATE TABLE nutrition_diary_entry (
 entry_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 user_id bigint NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
 logged_on date NOT NULL CHECK(logged_on BETWEEN DATE '1900-01-01' AND DATE '2100-12-31'),
 meal text NOT NULL CHECK(meal IN ('breakfast','lunch','dinner','snack')),
 food_id bigint,
 food_name varchar(100) NOT NULL CHECK(char_length(trim(food_name)) > 0),
 grams numeric(8,2) NOT NULL CHECK(grams BETWEEN 0.01 AND 10000),
 -- Snapshot source values, never computed/rounded per-entry totals.
 kcal_100g numeric(6,2) CHECK(kcal_100g BETWEEN 0 AND 1000),
 protein_100g numeric(5,2) CHECK(protein_100g BETWEEN 0 AND 100),
 carbs_100g numeric(5,2) CHECK(carbs_100g BETWEEN 0 AND 100),
 fat_100g numeric(5,2) CHECK(fat_100g BETWEEN 0 AND 100),
 FOREIGN KEY(food_id,user_id) REFERENCES nutrition_food(food_id,user_id) ON DELETE RESTRICT
);
CREATE INDEX nutrition_diary_user_date_idx ON nutrition_diary_entry(user_id,logged_on);

INSERT INTO schema_migration(version) VALUES ('003_nutrition') ON CONFLICT (version) DO NOTHING;
COMMIT;
