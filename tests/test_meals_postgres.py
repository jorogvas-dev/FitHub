"""End-to-end recipes, scoped mutations, draft snapshots and atomic meal logging."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from unittest.mock import patch
from flask import template_rendered
from test_plans_postgres import PostgresTestCase

DAY='2026-01-02'


class MealPlannerTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.post(self.a,'/nutrition/settings',timezone='Europe/Sofia',target_kcal=1600,target_protein=80,target_carbs=160,target_fat=40)
        self.post(self.a,'/nutrition/foods/new',name='Private oats',kcal_100g=400,protein_100g=20,carbs_100g=40,fat_100g=10)
        with self.connect_db() as conn:
            self.food=conn.execute('SELECT food_id FROM nutrition_food WHERE user_id=%s',(self.owner,)).fetchone()[0]

    def make_recipe(self,name='All-meal oats',**overrides):
        values=dict(name=name,servings=1,meals=['breakfast','lunch','dinner','snack'],allergen_complete='yes',dietary_tags=['vegetarian'])
        response=self.post(self.a,'/nutrition/recipes/new',**{**values,**overrides})
        self.assertEqual(response.status_code,302,response.data)
        recipe=int(response.location.rsplit('/',1)[1])
        response=self.post(self.a,f'/nutrition/recipes/{recipe}/ingredients/new',food_token=f'private:{self.food}',
                           grams=100,allergen_complete='yes',ingredient_labels='oats')
        self.assertEqual(response.status_code,302,response.data)
        return recipe

    def draft(self):
        response=self.post(self.a,'/nutrition/meal-plans/new',planned_on=DAY)
        self.assertEqual(response.status_code,302,response.data)
        return int(response.location.rsplit('/',1)[1])

    def meal_rows(self,plan):
        with self.connect_db() as conn:
            return conn.execute('SELECT meal_id,meal,portion,recipe_snapshot,logged_at FROM meal_plan_meal WHERE plan_id=%s ORDER BY meal_id',(plan,)).fetchall()

    def diary_rows(self):
        with self.connect_db() as conn:
            return conn.execute('SELECT logged_on,meal,food_name,grams,kcal_100g,protein_100g,carbs_100g,fat_100g,plan_meal_id,provenance FROM nutrition_diary_entry WHERE user_id=%s ORDER BY entry_id',(self.owner,)).fetchall()

    def settings(self,**overrides):
        return self.post(self.a,'/nutrition/settings',**{**dict(timezone='Europe/Sofia',target_kcal=1600,
                         target_protein=80,target_carbs=160,target_fat=40),**overrides})

    def test_full_recipe_draft_adjust_swap_save_and_explicit_log(self):
        recipe=self.make_recipe();plan=self.draft()
        rows=self.meal_rows(plan);self.assertEqual(len(rows),4);self.assertEqual(self.diary_rows(),[])
        paths=['/nutrition/recipes',f'/nutrition/recipes/{recipe}',f'/nutrition/recipes/{recipe}/edit',
               f'/nutrition/recipes/{recipe}/ingredients/new','/nutrition/meal-plans',f'/nutrition/meal-plans/{plan}',
               f'/nutrition/meal-plans/{plan}/meals/{rows[0][0]}/edit']
        for path in paths:self.assertEqual(self.a.get(path).status_code,200,path)
        edit=f'/nutrition/meal-plans/{plan}/meals/{rows[0][0]}/edit'
        self.assertEqual(self.post(self.a,edit,recipe_id='snapshot',portion='0.5').status_code,302)
        second=self.make_recipe('Second eligible recipe')
        self.assertEqual(self.post(self.a,edit,recipe_id=second,portion='0.75').status_code,302)
        self.assertEqual(self.diary_rows(),[])
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{plan}/save').status_code,400)
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{plan}/save',accept_differences='yes').status_code,302)
        self.assertEqual(self.diary_rows(),[])
        log=f'/nutrition/meal-plans/{plan}/meals/{rows[0][0]}/log'
        self.assertEqual(self.post(self.a,log).status_code,400)
        self.assertEqual(self.post(self.a,log,confirm='log').status_code,302)
        logged=self.diary_rows();self.assertEqual(len(logged),1)
        self.assertEqual(logged[0][3],D('75'))
        self.assertEqual(logged[0][4],D('400'))
        self.assertEqual(self.post(self.a,log,confirm='log').status_code,409)
        self.assertEqual(self.diary_rows(),logged)
        self.assertEqual(self.post(self.a,edit,recipe_id='snapshot',portion=1).status_code,409)
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{plan}/save',accept_differences='yes').status_code,409)

    def test_recipe_calculations_and_ingredient_refresh(self):
        recipe=self.make_recipe()
        captured=[]
        def capture(sender,template,context,**extra):captured.append(context)
        with template_rendered.connected_to(capture,self.app):
            self.assertEqual(self.a.get(f'/nutrition/recipes/{recipe}').status_code,200)
        self.assertEqual(captured[-1]['totals']['kcal'],D('400'))

        with self.connect_db() as conn:
            ingredient=conn.execute('SELECT ingredient_id FROM recipe_ingredient WHERE recipe_id=%s',(recipe,)).fetchone()[0]
        self.post(self.a,f'/nutrition/foods/{self.food}/edit',name='Updated food',kcal_100g=200,protein_100g=10,carbs_100g=20,fat_100g=5)
        path=f'/nutrition/recipes/{recipe}/ingredients/{ingredient}/edit'
        self.assertEqual(self.a.get(path).status_code,200)
        self.assertEqual(self.post(self.a,path,food_token=f'private:{self.food}',grams=200,allergen_complete='yes',ingredient_labels='oats').status_code,302)
        with template_rendered.connected_to(capture,self.app):self.a.get(f'/nutrition/recipes/{recipe}')
        self.assertEqual(captured[-1]['totals']['kcal'],D('400'))

    def test_ingredient_crud_confirmation_and_missing_values(self):
        recipe=self.make_recipe()
        with self.connect_db() as conn:
            ingredient=conn.execute('SELECT ingredient_id FROM recipe_ingredient WHERE recipe_id=%s',(recipe,)).fetchone()[0]
        delete=f'/nutrition/recipes/{recipe}/ingredients/{ingredient}/delete'
        self.assertEqual(self.a.get(delete).status_code,200)
        self.assertEqual(self.post(self.a,delete).status_code,400)
        for value in ['0','NaN','Infinity','10000.01','0.001']:
            self.assertEqual(self.post(self.a,f'/nutrition/recipes/{recipe}/ingredients/{ingredient}/edit',
                food_token=f'private:{self.food}',grams=value).status_code,200)
        self.assertEqual(self.post(self.a,delete,confirm='delete').status_code,302)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM recipe_ingredient WHERE recipe_id=%s',(recipe,)).fetchone()[0],0)
            conn.execute('UPDATE nutrition_food SET carbs_100g=NULL WHERE food_id=%s',(self.food,))
        self.assertEqual(self.post(self.a,f'/nutrition/recipes/{recipe}/ingredients/new',food_token=f'private:{self.food}',grams=100).status_code,302)
        self.assertIn(b'Unknown',self.a.get(f'/nutrition/recipes/{recipe}').data)
        self.assertIn(b'Unknown nutrition values',self.a.get('/nutrition/meal-plans/new').data)

    def test_library_unknown_allergens_require_independent_ingredient_review(self):
        recipe=self.make_recipe()
        with self.connect_db() as conn:
            catalogue=conn.execute("SELECT catalogue_id FROM food_catalogue WHERE source_id='171265'").fetchone()[0]
        self.post(self.a,f'/nutrition/recipes/{recipe}/ingredients/new',food_token=f'catalogue:{catalogue}',grams=100)
        self.settings(allergies='milk')
        page=self.a.get('/nutrition/meal-plans/new').data
        self.assertIn(b'Incomplete recipe or ingredient allergen metadata',page)
        # USDA nutrients did not create an allergen-free declaration.
        with self.connect_db() as conn:
            ingredient=conn.execute('SELECT ingredient_id FROM recipe_ingredient WHERE recipe_id=%s AND catalogue_id=%s',(recipe,catalogue)).fetchone()[0]
        self.post(self.a,f'/nutrition/recipes/{recipe}/ingredients/{ingredient}/edit',food_token=f'catalogue:{catalogue}',grams=100,
                  allergen_complete='yes',allergens=['milk'],ingredient_labels='milk')
        self.assertIn(b'Declared allergens: milk',self.a.get('/nutrition/meal-plans/new').data)

    def test_declared_source_allergens_cannot_be_dropped_by_ingredient_form(self):
        recipe=self.make_recipe()
        with self.connect_db() as conn:
            conn.execute("UPDATE nutrition_food SET allergens=ARRAY['milk'],allergen_complete=true WHERE food_id=%s",(self.food,))
        self.assertEqual(self.post(self.a,f'/nutrition/recipes/{recipe}/ingredients/new',food_token=f'private:{self.food}',
            grams=50,allergen_complete='yes',ingredient_labels='oats').status_code,302)
        with self.connect_db() as conn:
            allergens,snapshot=conn.execute('SELECT allergens,food_snapshot FROM recipe_ingredient WHERE recipe_id=%s ORDER BY ingredient_id DESC LIMIT 1',(recipe,)).fetchone()
            self.assertEqual(allergens,['milk'])
            self.assertEqual(snapshot['source_allergens'],['milk'])
        self.settings(allergies='milk')
        self.assertIn(b'Declared allergens: milk',self.post(self.a,'/nutrition/meal-plans/new',planned_on=DAY).data)

    def test_deactivated_library_food_cannot_be_new_ingredient_or_new_plan_source(self):
        recipe=self.make_recipe()
        with self.connect_db() as conn:
            catalogue=conn.execute("SELECT catalogue_id FROM food_catalogue WHERE source_id='171265'").fetchone()[0]
        self.post(self.a,f'/nutrition/recipes/{recipe}/ingredients/new',food_token=f'catalogue:{catalogue}',grams=100)
        plan=self.draft();row=self.meal_rows(plan)[0]
        self.post(self.a,f'/nutrition/meal-plans/{plan}/save',accept_differences='yes')
        try:
            with self.connect_db() as conn:conn.execute('UPDATE food_catalogue SET is_active=false WHERE catalogue_id=%s',(catalogue,))
            self.assertEqual(self.post(self.a,f'/nutrition/recipes/{recipe}/ingredients/new',food_token=f'catalogue:{catalogue}',grams=100).status_code,404)
            self.assertIn(b'ingredient source is deactivated',self.a.get('/nutrition/meal-plans/new').data)
            # Saved snapshots remain usable independently of future catalogue availability.
            self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{plan}/meals/{row[0]}/log',confirm='log').status_code,302)
            self.assertEqual(len(self.diary_rows()),2)
        finally:
            with self.connect_db() as conn:conn.execute('UPDATE food_catalogue SET is_active=true WHERE catalogue_id=%s',(catalogue,))

    def test_plan_and_diary_snapshots_survive_food_recipe_changes_and_deletion(self):
        recipe=self.make_recipe();plan=self.draft();before=self.meal_rows(plan)
        self.post(self.a,f'/nutrition/meal-plans/{plan}/save',accept_differences='yes')
        self.post(self.a,f'/nutrition/foods/{self.food}/edit',name='Changed source',kcal_100g=900,protein_100g=90,carbs_100g=90,fat_100g=90)
        self.post(self.a,f'/nutrition/recipes/{recipe}/edit',name='Changed recipe',servings=20,meals=['snack'])
        self.assertEqual(self.post(self.a,f'/nutrition/recipes/{recipe}/delete',confirm='delete').status_code,302)
        self.assertEqual(self.meal_rows(plan),before)
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{plan}/meals/{before[0][0]}/log',confirm='log').status_code,302)
        self.assertEqual(self.diary_rows()[0][2],'Private oats')
        self.assertEqual(self.diary_rows()[0][4],D('400'))
        self.assertIn(b'All-meal oats',self.a.get(f'/nutrition/meal-plans/{plan}').data)

    def test_exclusions_unknown_metadata_nutrients_and_preferences_are_explained(self):
        recipe=self.make_recipe()
        self.settings(allergies='peanuts',dietary_preferences='vegetarian',unwanted_ingredients='olives')
        self.assertEqual(self.a.get('/nutrition/meal-plans/new').status_code,200)
        with self.connect_db() as conn:
            conn.execute('UPDATE recipe_ingredient SET allergen_complete=false WHERE recipe_id=%s',(recipe,))
        response=self.post(self.a,'/nutrition/meal-plans/new',planned_on=DAY)
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Incomplete recipe or ingredient allergen metadata',response.data)
        self.assertIn(b'No eligible breakfast',response.data)
        with self.connect_db() as conn:
            conn.execute("UPDATE recipe_ingredient SET allergen_complete=true,allergens=ARRAY['peanut'] WHERE recipe_id=%s",(recipe,))
        self.assertIn(b'Declared allergens: peanut',self.a.get('/nutrition/meal-plans/new').data)
        self.settings(allergies='',dietary_preferences='vegan',unwanted_ingredients='oats')
        page=self.a.get('/nutrition/meal-plans/new').data
        self.assertIn(b'Unwanted ingredients: oats',page);self.assertIn(b'Missing required dietary',page)
        self.settings(allergies='unrecognised allergy',dietary_preferences='low carb')
        page=self.post(self.a,'/nutrition/meal-plans/new',planned_on=DAY).data
        self.assertIn(b'Unrecognised allergy',page);self.assertIn(b'Unrecognised dietary',page)
        self.settings()
        with self.connect_db() as conn:
            conn.execute("UPDATE recipe_ingredient SET food_snapshot=jsonb_set(food_snapshot,'{protein_100g}','null') WHERE recipe_id=%s",(recipe,))
        self.assertIn(b'Unknown nutrition values',self.post(self.a,'/nutrition/meal-plans/new',planned_on=DAY).data)
        self.assertEqual(self.diary_rows(),[])

    def test_infeasible_targets_explicit_differences_and_no_target_changes(self):
        self.make_recipe();self.settings(target_kcal=0,target_protein=0,target_carbs=0,target_fat=0)
        plan=self.draft();page=self.a.get(f'/nutrition/meal-plans/{plan}').data
        self.assertIn(b'Targets are not met',page);self.assertIn(b'outside the searched portion range',page)
        self.assertIn(b'400 kcal over',page)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT target_kcal FROM nutrition_settings WHERE user_id=%s',(self.owner,)).fetchone()[0],D(0))

    def test_ownership_child_ids_and_foreign_recipe_swap(self):
        recipe=self.make_recipe();other=self.make_recipe('Second');plan=self.draft();secondplan=self.draft()
        row=self.meal_rows(plan)[0]
        with self.connect_db() as conn:
            ingredient=conn.execute('SELECT ingredient_id FROM recipe_ingredient WHERE recipe_id=%s',(recipe,)).fetchone()[0]
        paths=[f'/nutrition/recipes/{recipe}',f'/nutrition/recipes/{recipe}/edit',f'/nutrition/recipes/{recipe}/delete',
               f'/nutrition/recipes/{recipe}/ingredients/new',f'/nutrition/recipes/{recipe}/ingredients/{ingredient}/edit',
               f'/nutrition/recipes/{recipe}/ingredients/{ingredient}/delete',f'/nutrition/meal-plans/{plan}',
               f'/nutrition/meal-plans/{plan}/save',f'/nutrition/meal-plans/{plan}/meals/{row[0]}/edit',
               f'/nutrition/meal-plans/{plan}/meals/{row[0]}/log']
        for path in paths:
            if not path.endswith(('/save','/log')):self.assertEqual(self.b.get(path).status_code,404,path)
            if path not in (f'/nutrition/recipes/{recipe}',f'/nutrition/meal-plans/{plan}'):
                self.assertEqual(self.post(self.b,path,confirm='delete',user_id=self.owner).status_code,404,path)
        for action in ['edit','delete']:
            self.assertEqual(self.post(self.a,f'/nutrition/recipes/{other}/ingredients/{ingredient}/{action}',confirm='delete').status_code,404)
        for action in ['edit','log']:
            self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{secondplan}/meals/{row[0]}/{action}',confirm='log').status_code,404)
        self.assertNotIn(b'All-meal oats',self.b.get('/nutrition/recipes').data)
        self.assertNotIn(f'Plan #{plan}'.encode(),self.b.get('/nutrition/meal-plans').data)
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{plan}/meals/{row[0]}/edit',recipe_id=999999,portion=1).status_code,404)
        # A private food cannot be smuggled into another owner's recipe.
        response=self.post(self.b,'/nutrition/recipes/new',name='Bob recipe',servings=1,meals=['snack'])
        bob_recipe=int(response.location.rsplit('/',1)[1])
        self.assertEqual(self.post(self.b,f'/nutrition/recipes/{bob_recipe}/ingredients/new',food_token=f'private:{self.food}',grams=100).status_code,404)

    def test_portion_validation_and_recipe_validation_are_atomic(self):
        recipe=self.make_recipe();plan=self.draft();row=self.meal_rows(plan)[0];original=self.meal_rows(plan)
        edit=f'/nutrition/meal-plans/{plan}/meals/{row[0]}/edit'
        for value in ['0','.24','2.01','1.1','NaN','Infinity','-1','1e0']:
            self.assertEqual(self.post(self.a,edit,portion=value,recipe_id='snapshot').status_code,200,value)
            self.assertEqual(self.meal_rows(plan),original)
        for values in [dict(name=''),dict(servings=0),dict(servings=21),dict(servings='1.5'),dict(meals=['brunch']),dict(allergens=['invented']),dict(dietary_tags=['low carb'])]:
            valid=dict(name='Valid',servings=1,meals=['breakfast'])
            self.assertEqual(self.post(self.a,f'/nutrition/recipes/{recipe}/edit',**{**valid,**values}).status_code,200)
        with self.connect_db() as conn:self.assertEqual(conn.execute('SELECT name FROM meal_recipe WHERE recipe_id=%s',(recipe,)).fetchone()[0],'All-meal oats')

    def test_new_restrictions_block_save_and_log_without_relaxing_old_ones(self):
        self.make_recipe();plan=self.draft();row=self.meal_rows(plan)[0]
        self.settings(unwanted_ingredients='oats')
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{plan}/save',accept_differences='yes').status_code,409)
        self.settings();self.post(self.a,f'/nutrition/meal-plans/{plan}/save',accept_differences='yes')
        self.settings(unwanted_ingredients='oats')
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{plan}/meals/{row[0]}/log',confirm='log').status_code,409)
        self.assertEqual(self.diary_rows(),[])

    def test_logging_rolls_back_all_ingredients_and_marker_on_failure(self):
        recipe=self.make_recipe()
        self.post(self.a,f'/nutrition/recipes/{recipe}/ingredients/new',food_token=f'private:{self.food}',grams=50)
        plan=self.draft();row=self.meal_rows(plan)[0]
        self.post(self.a,f'/nutrition/meal-plans/{plan}/save',accept_differences='yes')
        from app.routes.nutrition import insert_entry
        calls=0
        def fail_second(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('injected second ingredient failure')
            return insert_entry(*args,**kwargs)
        with patch('app.routes.meals.insert_entry',side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError,'injected second'):
                self.post(self.a,f'/nutrition/meal-plans/{plan}/meals/{row[0]}/log',confirm='log')
        self.assertEqual(self.diary_rows(),[])
        self.assertIsNone(self.meal_rows(plan)[0][4])

    def test_concurrent_duplicate_logging_and_deleted_diary_entry_do_not_relog(self):
        self.make_recipe();plan=self.draft();row=self.meal_rows(plan)[0]
        self.post(self.a,f'/nutrition/meal-plans/{plan}/save',accept_differences='yes')
        clients=[self.app.test_client(),self.app.test_client()]
        for client in clients:
            with client.session_transaction() as state:state['user_id']=self.owner;state['csrf_token']='concurrent-log'
        path=f'/nutrition/meal-plans/{plan}/meals/{row[0]}/log'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(self.post,client,path,confirm='log') for client in clients]
            self.assertEqual(sorted(f.result(timeout=10).status_code for f in futures),[302,409])
        self.assertEqual(len(self.diary_rows()),1)
        with self.connect_db() as conn:conn.execute('DELETE FROM nutrition_diary_entry WHERE user_id=%s',(self.owner,))
        self.assertEqual(self.post(self.a,path,confirm='log').status_code,409)
        self.assertEqual(self.diary_rows(),[])

    def test_authentication_csrf_post_only_and_delete_confirmation(self):
        recipe=self.make_recipe();plan=self.draft();row=self.meal_rows(plan)[0]
        paths=['/nutrition/recipes/new',f'/nutrition/recipes/{recipe}/edit',f'/nutrition/recipes/{recipe}/delete',
               f'/nutrition/recipes/{recipe}/ingredients/new','/nutrition/meal-plans/new',
               f'/nutrition/meal-plans/{plan}/save',f'/nutrition/meal-plans/{plan}/meals/{row[0]}/edit',
               f'/nutrition/meal-plans/{plan}/meals/{row[0]}/log']
        anon=self.app.test_client()
        for path in paths:
            self.assertEqual(self.a.post(path).status_code,400,path)
            self.assertTrue(self.post(anon,path).location.endswith('/login'),path)
            self.assertEqual(self.a.delete(path).status_code,405,path)
        for path in [f'/nutrition/meal-plans/{plan}/save',f'/nutrition/meal-plans/{plan}/meals/{row[0]}/log']:
            self.assertEqual(self.a.get(path).status_code,405)
        self.assertEqual(self.post(self.a,f'/nutrition/recipes/{recipe}/delete').status_code,400)
        self.assertEqual(self.a.get(f'/nutrition/recipes/{recipe}').status_code,200)

    def test_shared_food_ingredient_snapshot_survives_catalogue_edits(self):
        recipe=self.make_recipe()
        with self.connect_db() as conn:
            catalogue=conn.execute("SELECT catalogue_id FROM food_catalogue WHERE source_id='171265'").fetchone()[0]
            original=conn.execute('SELECT name,kcal_100g FROM food_catalogue WHERE catalogue_id=%s',(catalogue,)).fetchone()
        self.assertEqual(self.post(self.a,f'/nutrition/recipes/{recipe}/ingredients/new',food_token=f'catalogue:{catalogue}',grams=100).status_code,302)
        plan=self.draft();before=self.meal_rows(plan)
        self.post(self.a,f'/nutrition/meal-plans/{plan}/save',accept_differences='yes')
        try:
            with self.connect_db() as conn:conn.execute("UPDATE food_catalogue SET name='Changed milk',kcal_100g=999 WHERE catalogue_id=%s",(catalogue,))
            self.assertEqual(self.meal_rows(plan),before)
            self.post(self.a,f'/nutrition/meal-plans/{plan}/meals/{before[0][0]}/log',confirm='log')
            self.assertEqual(len(self.diary_rows()),2)
            self.assertEqual(self.diary_rows()[1][2],original[0]);self.assertEqual(self.diary_rows()[1][4],original[1])
        finally:
            with self.connect_db() as conn:conn.execute('UPDATE food_catalogue SET name=%s,kcal_100g=%s WHERE catalogue_id=%s',(*original,catalogue))
