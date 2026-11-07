"""Starter drafts reuse the real planner, library values and owner-scoped save/log workflows."""
from decimal import Decimal as D
from flask import template_rendered
from app.cli import upgrade
from app.services.meal_planner import exclusion_reasons, portion_entries, totals
from app.services.starter_recipes import candidates, INGREDIENTS
from test_plans_postgres import PostgresTestCase

DAY='2026-11-07'


class NutritionStarterTests(PostgresTestCase):
    def counts(self):
        with self.connect_db() as conn:
            return tuple(conn.execute("SELECT (SELECT count(*) FROM meal_plan WHERE user_id=%s AND status='draft'),"
                "(SELECT count(*) FROM meal_plan WHERE user_id=%s AND status='saved'),"
                '(SELECT count(*) FROM nutrition_diary_entry WHERE user_id=%s)',(self.owner,)*3).fetchone())

    def settings(self,**values):
        return self.post(self.a,'/nutrition/settings',timezone='Europe/Sofia',**values)

    def draft(self):
        response=self.post(self.a,'/nutrition/meal-plans/new',planned_on=DAY,include_starters='yes')
        self.assertEqual(response.status_code,302,response.data)
        return response.location

    def rows(self,path):
        with self.connect_db() as conn:
            return conn.execute('SELECT meal_id,meal,portion,recipe_snapshot FROM meal_plan_meal WHERE plan_id=%s ORDER BY meal_id',
                                (int(path.rsplit('/',1)[1]),)).fetchall()

    def test_new_account_onboarding_unset_targets_review_swap_save_and_separate_log(self):
        self.assertIn(b'Generate nutrition plan',self.a.get('/onboarding').data)
        self.assertIn(b'Generate nutrition plan',self.a.get('/onboarding/training').data)
        page=self.a.get('/nutrition/meal-plans/new')
        self.assertIn(b'Include FitHub starter recipes',page.data)
        self.assertEqual(self.counts(),(0,0,0))
        path=self.draft()
        self.assertEqual(self.counts(),(1,0,0))
        page=self.a.get(path)
        self.assertIn(b'No targets set',page.data)
        self.assertEqual(page.data.count(b'Target not set'),4)
        self.assertNotIn(b'Targets are not met',page.data)
        rows=self.rows(path)
        self.assertEqual(len(rows),3)
        self.assertTrue(all(row[2]==D(1) for row in rows))
        self.assertTrue(all(row[3]['starter'] for row in rows))
        breakfast=rows[0]
        edit=path+f'/meals/{breakfast[0]}/edit'
        self.assertEqual(self.post(self.a,edit,recipe_id='starter:yogurt-berries',portion='0.75').status_code,302)
        self.assertEqual(self.rows(path)[0][2],D('.75'))
        self.assertEqual(self.counts(),(1,0,0))
        self.assertEqual(self.post(self.a,path+f'/meals/{breakfast[0]}/log',confirm='log').status_code,409)
        self.assertEqual(self.a.get(path+'/save').status_code,405)
        self.assertEqual(self.a.post(path+'/save').status_code,400)
        self.assertEqual(self.post(self.a,path+'/save').status_code,302)
        self.assertEqual(self.counts(),(0,1,0))
        self.assertEqual(self.post(self.a,path+f'/meals/{breakfast[0]}/log').status_code,400)
        self.assertEqual(self.post(self.a,path+f'/meals/{breakfast[0]}/log',confirm='log').status_code,302)
        self.assertEqual(self.counts(),(0,1,2))
        with self.connect_db() as conn:
            self.assertIsNone(conn.execute('SELECT target_kcal FROM nutrition_settings WHERE user_id=%s',(self.owner,)).fetchone())

    def test_verified_ingredients_nutrients_preparation_and_provenance_are_reused(self):
        with self.connect_db() as conn:
            recipes,excluded=candidates(conn,dict(allergies=[],unwanted=[],diets=[]))
            self.assertEqual((len(recipes),len(excluded)),(8,0))
            for recipe in recipes:
                for ingredient in recipe['ingredients']:
                    food=ingredient['food']
                    source=food['provenance']['source_id']
                    self.assertIn(source,INGREDIENTS)
                    value=conn.execute('SELECT name,preparation,kcal_100g FROM food_catalogue WHERE source_id=%s',(source,)).fetchone()
                    self.assertEqual((food['name'],food['provenance']['preparation']),(value[0],value[1]))
                    self.assertEqual(D(food['kcal_100g']),value[2])
                self.assertTrue(all(value is not None for value in totals(portion_entries(recipe,D(1))).values()))

    def test_milk_peanut_and_soy_allergies_exclude_meals_and_forged_swaps(self):
        self.settings(allergies='dairy, peanuts, soya')
        page=self.a.get('/nutrition/meal-plans/new?starters=yes').data
        for label in [b'Declared allergens: milk',b'Declared allergens: peanut',b'Declared allergens: soy']:
            self.assertIn(label,page)
        path=self.draft()
        rows=self.rows(path)
        rules=dict(allergies=['milk','peanut','soy'],unwanted=[],diets=[])
        self.assertTrue(all(not exclusion_reasons(row[3],rules,require_nutrition=False) for row in rows))
        self.assertEqual(self.post(self.a,path+f'/meals/{rows[0][0]}/edit',recipe_id='starter:yogurt-berries',portion=1).status_code,404)
        self.assertEqual(self.counts(),(1,0,0))

    def test_impossible_allergies_excluded_foods_and_unknown_preferences_explain_the_blocker(self):
        for settings,message in [
            (dict(allergies='milk, gluten'),b'No eligible breakfast'),
            (dict(unwanted_ingredients='rice'),b'No eligible lunch'),
            (dict(dietary_preferences='low carb'),b'Unrecognised dietary preferences'),
            (dict(allergies='unknown allergy'),b'Unrecognised allergy labels')]:
            self.settings(**settings)
            page=self.post(self.a,'/nutrition/meal-plans/new',planned_on=DAY,include_starters='yes')
            if page.status_code==302:
                preview=self.a.get(page.location)
                self.assertIn(b'Partial day',preview.data)
                page=self.a.get('/nutrition/meal-plans/new?starters=yes')
            else:
                self.assertEqual(page.status_code,200)
                self.assertIn(message,page.data)
            if 'allergies' in settings and 'gluten' in settings['allergies']:
                self.assertIn(b'Declared allergens: gluten',page.data)
                self.assertIn(b'Declared allergens: milk',page.data)
            if 'unwanted_ingredients' in settings:
                self.assertIn(b'Unwanted ingredients: rice',page.data)
        self.assertEqual(self.counts(),(2,0,0))

    def test_vegan_and_excluded_food_preferences_remain_in_force(self):
        self.settings(dietary_preferences='vegan',unwanted_ingredients='soy, peanuts')
        path=self.draft()
        self.assertTrue(all(not exclusion_reasons(row[3],dict(allergies=[],unwanted=['soy','peanuts'],diets=['vegan']),require_nutrition=False) for row in self.rows(path)))

    def test_partial_and_zero_targets_are_preserved_without_inventing_missing_macros(self):
        for value in ('1800.25','0'):
            self.settings(target_kcal=value)
            path=self.draft()
            self.assertEqual(self.a.get(path).data.count(b'Target not set'),3)
            with self.connect_db() as conn:
                targets=conn.execute('SELECT targets FROM meal_plan WHERE plan_id=%s',(int(path.rsplit('/',1)[1]),)).fetchone()[0]
                self.assertEqual(D(targets['kcal']),D(value))
                self.assertTrue(all(targets[key] is None for key in ['protein','carbs','fat']))
                self.assertEqual(conn.execute('SELECT target_kcal,target_protein FROM nutrition_settings WHERE user_id=%s',(self.owner,)).fetchone(),(D(value),None))

    def test_changed_allergy_blocks_save_and_logging_of_starter_snapshots(self):
        path=self.draft()
        breakfast=self.rows(path)[0]
        edit=path+f'/meals/{breakfast[0]}/edit'
        self.post(self.a,edit,recipe_id='starter:yogurt-berries',portion=1)
        self.settings(allergies='milk')
        self.assertEqual(self.post(self.a,path+'/save').status_code,409)
        self.settings()
        self.assertEqual(self.post(self.a,path+'/save').status_code,302)
        self.settings(allergies='milk')
        self.assertEqual(self.post(self.a,path+f'/meals/{breakfast[0]}/log',confirm='log').status_code,409)
        self.assertEqual(self.counts(),(0,1,0))

    def test_inactive_unknown_and_changed_ingredient_sources_are_blocked(self):
        self.settings(target_protein='100')
        for statement,message in [
            ("UPDATE food_catalogue SET is_active=false WHERE source_id='169705'",b'deactivated'),
            ("UPDATE food_catalogue SET protein_100g=NULL WHERE source_id='169705'",b'Unknown nutrition'),
            ("UPDATE food_catalogue SET allergens=ARRAY['milk'] WHERE source_id='169705'",b'allergen declarations changed'),
            ("UPDATE food_catalogue SET name='Changed oats' WHERE source_id='169705'",b'review required')]:
            with self.connect_db() as conn:
                conn.execute(statement)
            page=self.a.get('/nutrition/meal-plans/new?starters=yes')
            self.assertIn(message,page.data)
            with self.connect_db() as conn:
                conn.execute("UPDATE food_catalogue SET name=%s,is_active=true,protein_100g=16.89,allergens='{}' WHERE source_id='169705'",
                    ("Oats (Includes foods for USDA's Food Distribution Program)",))

    def test_private_ownership_preferences_return_and_migration_preservation(self):
        response=self.post(self.a,'/nutrition/settings?next=meal-plan',allergies='milk',timezone='Europe/Sofia')
        self.assertTrue(response.location.endswith('/nutrition/meal-plans/new?starters=yes'))
        path=self.draft()
        self.assertEqual(self.b.get(path).status_code,404)
        self.assertEqual(self.post(self.b,path+'/save').status_code,404)
        with self.connect_db() as conn:
            before=conn.execute('SELECT plan_id,status,targets,restrictions FROM meal_plan').fetchall()
            conn.execute('ALTER TABLE meal_plan DROP COLUMN include_starters')
            conn.execute("DELETE FROM schema_migration WHERE version='008_nutrition_starters'")
            self.assertEqual(upgrade(conn),['008_nutrition_starters'])
            self.assertEqual(upgrade(conn),[])
            self.assertEqual(conn.execute('SELECT plan_id,status,targets,restrictions FROM meal_plan').fetchall(),before)
