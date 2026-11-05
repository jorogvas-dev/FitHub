CREATE TABLE meal_recipe (
 recipe_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 user_id bigint NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
 name varchar(100) NOT NULL CHECK(char_length(trim(name))>0),
 servings integer NOT NULL CHECK(servings BETWEEN 1 AND 20),
 meals text[] NOT NULL CHECK(cardinality(meals)>0 AND meals <@ ARRAY['breakfast','lunch','dinner','snack']),
 dietary_tags text[] NOT NULL DEFAULT '{}' CHECK(dietary_tags <@ ARRAY['vegetarian','vegan','pescatarian']),
 allergens text[] NOT NULL DEFAULT '{}',
 allergen_complete boolean NOT NULL DEFAULT false,
 UNIQUE(recipe_id,user_id)
);
CREATE TABLE recipe_ingredient (
 ingredient_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 recipe_id bigint NOT NULL,
 user_id bigint NOT NULL,
 food_id bigint,
 catalogue_id bigint REFERENCES food_catalogue(catalogue_id) ON DELETE RESTRICT,
 grams numeric(8,2) NOT NULL CHECK(grams BETWEEN 0.01 AND 10000),
 food_snapshot jsonb NOT NULL,
 allergens text[] NOT NULL DEFAULT '{}',
 allergen_complete boolean NOT NULL DEFAULT false,
 ingredient_labels text[] NOT NULL DEFAULT '{}',
 CHECK((food_id IS NULL) <> (catalogue_id IS NULL)),
 FOREIGN KEY(recipe_id,user_id) REFERENCES meal_recipe(recipe_id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(food_id,user_id) REFERENCES nutrition_food(food_id,user_id) ON DELETE RESTRICT
);
CREATE INDEX recipe_ingredient_parent_idx ON recipe_ingredient(recipe_id,user_id);
CREATE TABLE meal_plan (
 plan_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 user_id bigint NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
 planned_on date NOT NULL CHECK(planned_on BETWEEN DATE '1900-01-01' AND DATE '2100-12-31'),
 status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','saved')),
 targets jsonb NOT NULL,
 restrictions jsonb NOT NULL,
 search_note text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(),
 saved_at timestamptz,
 UNIQUE(plan_id,user_id),
 CHECK((status='saved')=(saved_at IS NOT NULL))
);
CREATE TABLE meal_plan_meal (
 meal_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 plan_id bigint NOT NULL,
 user_id bigint NOT NULL,
 meal text NOT NULL CHECK(meal IN ('breakfast','lunch','dinner','snack')),
 portion numeric(3,2) NOT NULL CHECK(portion BETWEEN 0.25 AND 2 AND mod(portion,0.25)=0),
 recipe_snapshot jsonb NOT NULL,
 logged_at timestamptz,
 FOREIGN KEY(plan_id,user_id) REFERENCES meal_plan(plan_id,user_id) ON DELETE CASCADE,
 UNIQUE(plan_id,meal),
 UNIQUE(meal_id,user_id)
);
-- Permanent log marker plus unique ingredient positions prevent double-click/race duplicates.
ALTER TABLE nutrition_diary_entry
 ADD COLUMN plan_meal_id bigint,
 ADD COLUMN plan_ingredient_index integer,
 ADD CONSTRAINT diary_plan_owner_fk FOREIGN KEY(plan_meal_id,user_id)
   REFERENCES meal_plan_meal(meal_id,user_id) ON DELETE RESTRICT,
 ADD CONSTRAINT diary_plan_position_unique UNIQUE(plan_meal_id,plan_ingredient_index),
 ADD CONSTRAINT diary_plan_position_check CHECK((plan_meal_id IS NULL)=(plan_ingredient_index IS NULL));
CREATE INDEX meal_recipe_owner_idx ON meal_recipe(user_id);
CREATE INDEX meal_plan_owner_date_idx ON meal_plan(user_id,planned_on DESC,plan_id DESC);
