CREATE TABLE IF NOT EXISTS recipe_catalogue (
 recipe_id text PRIMARY KEY,
 source text NOT NULL,
 source_id text NOT NULL,
 name text NOT NULL,
 description text NOT NULL,
 servings integer NOT NULL CHECK(servings BETWEEN 1 AND 100),
 meals text[] NOT NULL,
 dietary_tags text[] NOT NULL DEFAULT '{}',
 tags text[] NOT NULL,
 ingredients jsonb NOT NULL CHECK(jsonb_typeof(ingredients)='array'),
 instructions jsonb NOT NULL CHECK(jsonb_typeof(instructions)='array'),
 macros jsonb NOT NULL CHECK(jsonb_typeof(macros)='object'),
 provenance jsonb NOT NULL,
 content_sha256 text NOT NULL,
 is_active boolean NOT NULL DEFAULT true,
 UNIQUE(source,source_id)
);
ALTER TABLE meal_plan ADD COLUMN IF NOT EXISTS include_catalogue boolean NOT NULL DEFAULT false;
