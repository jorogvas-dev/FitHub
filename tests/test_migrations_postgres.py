"""Repeatable upgrades and seeding use the same files as the deployed CLI."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from psycopg.errors import DivisionByZero
from app.cli import seed_exercises, upgrade
from test_plans_postgres import PostgresTestCase


class MigrationTests(PostgresTestCase):
    def test_upgrade_nutrition_schema_preserves_foods_and_diary_ids_unknowns_and_values(self):
        with self.connect_db() as conn:
            identifier=conn.execute("INSERT INTO nutrition_food(user_id,name,kcal_100g,carbs_100g) VALUES (%s,'Original private food',123.45,0) RETURNING food_id",(self.owner,)).fetchone()[0]
            conn.execute("INSERT INTO nutrition_diary_entry(user_id,logged_on,meal,food_id,food_name,grams,kcal_100g,protein_100g) VALUES (%s,'2026-01-02','lunch',%s,'Original snapshot',50,123.45,NULL)",(self.owner,identifier))
            before_food=conn.execute('SELECT food_id,user_id,name,kcal_100g,protein_100g,carbs_100g,fat_100g FROM nutrition_food').fetchall()
            before_diary=conn.execute('SELECT entry_id,user_id,logged_on,meal,food_id,food_name,grams,kcal_100g,protein_100g FROM nutrition_diary_entry').fetchall()
            # Reconstruct migration 003 only in this isolated test schema.
            conn.execute('ALTER TABLE nutrition_diary_entry DROP COLUMN plan_meal_id,DROP COLUMN plan_ingredient_index')
            conn.execute('DROP TABLE meal_plan_meal,meal_plan,recipe_ingredient,meal_recipe')
            conn.execute('ALTER TABLE nutrition_diary_entry DROP COLUMN catalogue_id,DROP COLUMN provenance')
            conn.execute('ALTER TABLE nutrition_food DROP COLUMN provenance,DROP COLUMN allergens,DROP COLUMN allergen_complete,DROP COLUMN ingredient_labels')
            conn.execute('DROP TABLE food_catalogue')
            conn.execute("DELETE FROM schema_migration WHERE version IN ('004_food_catalogue','005_meal_planner','006_usda_source_links')")
            self.assertEqual(upgrade(conn),['004_food_catalogue','005_meal_planner','006_usda_source_links'])
            self.assertEqual(upgrade(conn),[])
            self.assertEqual(conn.execute('SELECT food_id,user_id,name,kcal_100g,protein_100g,carbs_100g,fat_100g FROM nutrition_food').fetchall(),before_food)
            self.assertEqual(conn.execute('SELECT entry_id,user_id,logged_on,meal,food_id,food_name,grams,kcal_100g,protein_100g FROM nutrition_diary_entry').fetchall(),before_diary)
            self.assertEqual(conn.execute('SELECT count(*) FROM food_catalogue').fetchone()[0],100)

    def test_fresh_install_has_distinct_catalogue_and_migration_parity(self):
        with self.connect_db() as conn:
            rows=conn.execute('SELECT name,muscle_group,equipment,description,tracking_type FROM exercise').fetchall()
            self.assertGreaterEqual(len({row[0].casefold() for row in rows}),50)
            self.assertTrue(all(all(value for value in row) for row in rows))
            self.assertTrue(any('machine' in row[2].lower() for row in rows))
            self.assertTrue(any('Dumbbell' in row[2] for row in rows))
            self.assertTrue(any(row[2]=='Bodyweight' for row in rows))
            self.assertEqual(conn.execute("SELECT tracking_type FROM exercise WHERE name='Plank'").fetchone(),('duration',))
            self.assertEqual(upgrade(conn),[])
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migration').fetchone()[0],6)

    def test_seed_preserves_custom_entries_ids_edits_and_inactive_status(self):
        with self.connect_db() as conn:
            conn.execute("UPDATE exercise SET description='Custom coaching text',is_active=false WHERE name='Cable row'")
            conn.execute("INSERT INTO exercise(name,muscle_group,equipment,description,is_active) VALUES ('School custom exercise','Back','Rings','Custom instructions',false)")
            before=conn.execute('SELECT * FROM exercise ORDER BY exercise_id').fetchall()
            seed_exercises(conn); seed_exercises(conn)
            self.assertEqual(conn.execute('SELECT * FROM exercise ORDER BY exercise_id').fetchall(),before)
        result=self.app.test_cli_runner().invoke(args=['seed-exercises'])
        self.assertEqual(result.exit_code,0,result.output)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT * FROM exercise ORDER BY exercise_id').fetchall(),before)

    def test_upgrade_original_schema_preserves_existing_records_and_is_repeatable(self):
        plan=self.create_plan(); day=self.create_day(plan); self.create_entry(plan,day)
        workout=int(self.post(self.a,f'/workouts/start/{plan}/{day}').location.rsplit('/',1)[1])
        self.post(self.a,f'/workouts/{workout}/sets/new',exercise_id=self.exercise,set_number=1,reps=8)
        with self.connect_db() as conn:
            # Reconstruct the original ten-exercise schema only in this test's isolated schema.
            conn.execute('DELETE FROM exercise WHERE exercise_id NOT IN (SELECT exercise_id FROM exercise ORDER BY exercise_id LIMIT 10)')
            conn.execute('ALTER TABLE exercise DROP COLUMN is_active,DROP COLUMN tracking_type')
            conn.execute('DROP TABLE nutrition_diary_entry,meal_plan_meal,meal_plan,recipe_ingredient,meal_recipe,nutrition_food,food_catalogue,nutrition_settings')
            conn.execute('DROP TABLE schema_migration')
            before={table:conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()
                    for table in ['app_user','fitness_profile','workout_plan','workout_day','plan_exercise','workout_session','set_log','body_measurement']}
            exercises=conn.execute('SELECT exercise_id,name,muscle_group,equipment,description FROM exercise ORDER BY exercise_id').fetchall()
            self.assertEqual(upgrade(conn),['001_exercise_lifecycle','002_exercise_library','003_nutrition','004_food_catalogue','005_meal_planner','006_usda_source_links'])
            self.assertEqual(upgrade(conn),[])
            for table,rows in before.items():
                self.assertEqual(conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall(),rows,table)
            self.assertEqual(conn.execute('SELECT exercise_id,name,muscle_group,equipment,description FROM exercise ORDER BY exercise_id LIMIT 10').fetchall(),exercises)
            self.assertGreaterEqual(conn.execute('SELECT count(*) FROM exercise').fetchone()[0],50)
        result=self.app.test_cli_runner().invoke(args=['db-upgrade'])
        self.assertEqual(result.exit_code,0,result.output)
        self.assertIn('up to date',result.output)

    def test_failed_migration_rolls_back_ddl_and_ledger(self):
        with TemporaryDirectory() as temporary:
            directory=Path(temporary); (directory/'migrations').mkdir()
            (directory/'migrations/007_failure.sql').write_text('ALTER TABLE exercise ADD COLUMN should_rollback text; SELECT 1/0;')
            with patch('app.cli.DB_ROOT',directory):
                with self.assertRaises(DivisionByZero):
                    with self.connect_db() as conn:
                        upgrade(conn)
            with self.connect_db() as conn:
                self.assertIsNone(conn.execute("SELECT version FROM schema_migration WHERE version='007_failure'").fetchone())
                self.assertIsNone(conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='exercise' AND column_name='should_rollback'").fetchone())
