"""Current target validation across generation, edits, swaps, save and precision."""
from decimal import Decimal as D
import json
import re
from unittest.mock import patch
from psycopg.types.json import Jsonb
from app.services import recipe_catalogue, meal_swaps
from test_plans_postgres import PostgresTestCase


class NutritionTargetIntegrationTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        with self.connect_db() as conn:conn.execute('DELETE FROM recipe_catalogue')

    def settings(self,**changes):
        self.assertEqual(self.post(self.a,'/nutrition/settings',**dict(timezone='Europe/Sofia',**changes)).status_code,302)

    def recipe(self,key,kcal='100',protein='20',complete=True):
        ingredients=[dict(quantity='100',unit='g',note='',food=dict(name='Rice',source_allergens=[]),allergens=[],allergen_complete=complete,ingredient_labels=['rice'])]
        with self.connect_db() as conn:
            conn.execute("INSERT INTO recipe_catalogue(recipe_id,source,source_id,name,description,servings,meals,tags,ingredients,instructions,macros,provenance,content_sha256,prep_minutes,cook_minutes) VALUES (%s,'limits-test',%s,%s,'Sample',1,ARRAY['breakfast','lunch','dinner','snack'],ARRAY['test'],%s,%s,%s,%s,'test',10,0)",
                ('unitools:'+key,key,key,Jsonb(ingredients),Jsonb([dict(text='Cook rice.',minutes=10)]),Jsonb(dict(kcal=kcal,protein=protein,carbs='10',fat='5')),Jsonb(dict(commit='test-revision',homepage='https://example.invalid',license_url='https://example.invalid',attribution='Synthetic recipe'))))
            from app.routes.plans import query
            return recipe_catalogue.snapshot(query(conn,'SELECT * FROM recipe_catalogue WHERE recipe_id=%s',('unitools:'+key,),one=True))

    def plan(self,snapshots,saved=False):
        with self.connect_db() as conn:
            pid=conn.execute("INSERT INTO meal_plan(user_id,planned_on,status,saved_at,targets,restrictions,include_catalogue) VALUES (%s,'2026-11-07',%s,CASE WHEN %s THEN now() ELSE NULL END,%s,%s,true) RETURNING plan_id",
                (self.owner,'saved' if saved else 'draft',saved,Jsonb(dict(kcal=None,protein=None,carbs=None,fat=None)),Jsonb(dict(allergies=[],unwanted=[],diets=[])))).fetchone()[0]
            ids=[]
            for slot,snapshot in zip(['breakfast','lunch','dinner','snack'],snapshots):
                ids.append(conn.execute('INSERT INTO meal_plan_meal(plan_id,user_id,meal,portion,recipe_snapshot) VALUES (%s,%s,%s,1,%s) RETURNING meal_id',(pid,self.owner,slot,Jsonb(snapshot))).fetchone()[0])
        return pid,ids

    def preview(self,path):
        return re.search(r'name="limits_preview" value="([^"]+)"',self.a.get(path).get_data(as_text=True)).group(1)

    def test_fractional_overage_cannot_be_saved_even_when_it_displays_as_the_limit(self):
        self.settings(target_kcal='100')
        row=self.recipe('fractional',kcal='100.004')
        pid,ids=self.plan([row])
        path=f'/nutrition/meal-plans/{pid}'
        page=self.a.get(path)
        self.assertIn(b'100.004',page.data)  # Exact validation explains the hidden overage.
        self.assertEqual(self.post(self.a,path+'/save',accept_differences='yes').status_code,409)
        edit=path+f'/meals/{ids[0]}/edit'
        self.assertEqual(self.post(self.a,edit,recipe_id='snapshot',portion='1.25').status_code,200)
        self.assertEqual(self.post(self.a,edit,recipe_id='snapshot',portion='0.75').status_code,302)
        self.assertEqual(self.post(self.a,path+'/save',accept_differences='yes').status_code,302)
        with self.connect_db() as conn:self.assertEqual(conn.execute('SELECT portion FROM meal_plan_meal WHERE meal_id=%s',(ids[0],)).fetchone()[0],D('.75'))

    def test_changed_targets_and_exclusions_invalidate_save_and_edit_previews(self):
        self.settings(target_kcal='200')
        pid,ids=self.plan([self.recipe('plain')])
        path=f'/nutrition/meal-plans/{pid}'
        old=self.preview(path)
        edit=path+f'/meals/{ids[0]}/edit'
        old_edit=self.preview(edit)
        self.settings(target_kcal='50')
        self.assertEqual(self.post(self.a,path+'/save',limits_preview=old,accept_differences='yes').status_code,409)
        self.assertEqual(self.post(self.a,edit,limits_preview=old_edit,recipe_id='snapshot',portion='.25').status_code,409)
        self.assertEqual(self.post(self.a,path+'/save',accept_differences='yes').status_code,409)
        self.settings(target_kcal='150')
        self.assertEqual(self.post(self.a,path+'/save',accept_differences='yes').status_code,302)
        with self.connect_db() as conn:self.assertEqual(conn.execute('SELECT target_kcal FROM nutrition_settings WHERE user_id=%s',(self.owner,)).fetchone()[0],D(150))

    def test_unknown_nutrient_is_allowed_only_when_its_target_is_unset(self):
        self.settings(target_kcal='100')
        row=self.recipe('unknown',protein=None)
        pid,_=self.plan([row]);path=f'/nutrition/meal-plans/{pid}'
        self.assertIn(b'Unknown',self.a.get(path).data)
        self.assertEqual(self.post(self.a,path+'/save').status_code,302)
        self.settings(target_kcal='100',target_protein='30')
        pid,_=self.plan([row]);path=f'/nutrition/meal-plans/{pid}'
        self.assertEqual(self.post(self.a,path+'/save',accept_differences='yes').status_code,409)

    def test_swaps_respect_whole_day_caps_and_changed_targets_make_tokens_stale(self):
        self.settings(target_kcal='200')
        current=self.recipe('current',protein='20')
        candidate=self.recipe('alternative',kcal='110',protein='30')
        pid,ids=self.plan([current,current],saved=True)
        path=f'/nutrition/meal-plans/{pid}/meals/{ids[0]}'
        captured=[]
        from flask import template_rendered
        def capture(sender,template,context,**extra):captured.append(context)
        with template_rendered.connected_to(capture,self.app):response=self.a.get(path+'/swap?goal=protein')
        self.assertEqual(response.status_code,200)
        options=captured[-1]['options'];self.assertTrue(options)
        self.assertTrue(all(option['totals']['kcal']+D(100)<=200 for option in options))
        old=options[0]['token']
        self.settings(target_kcal='250')
        self.assertEqual(self.post(self.a,path+'/replace',preview=old,confirm='replace').status_code,409)

    def test_server_fits_model_proposals_and_rejects_allergy_unknown_snacks(self):
        self.settings(target_kcal='100',allergies='peanut')
        safe=self.recipe('safe',kcal='100.004')
        unsafe=self.recipe('unreviewed',kcal='10',complete=False)
        seen=[]
        def propose(context,schema):
            seen.extend(context['eligible_recipes'])
            return json.dumps(dict(meals={slot:dict(recipe_id=safe['recipe_id'],portion='2') for slot in ['breakfast','lunch','dinner','snack']}))
        with patch('app.services.recipe_index.rank',side_effect=lambda rows,text,**kwargs:rows),patch('app.services.workout_ai.nutrition',side_effect=propose):
            response=self.post(self.a,'/nutrition/meal-plans/ai',planned_on='2026-11-07')
        self.assertEqual(response.status_code,302)
        self.assertNotIn(unsafe['recipe_id'],[row['recipe_id'] for row in seen])
        with self.connect_db() as conn:
            entries=conn.execute('SELECT recipe_snapshot,portion FROM meal_plan_meal WHERE user_id=%s',(self.owner,)).fetchall()
            from app.services.meal_planner import totals,portion_entries
            total=totals([entry for row,portion in entries for entry in portion_entries(row,portion)])['kcal']
            self.assertLessEqual(total,D(100))
            self.assertEqual(conn.execute('SELECT count(*) FROM nutrition_diary_entry').fetchone()[0],0)

    def test_fractional_grams_are_logged_and_loaded_for_edit_without_rounding(self):
        self.settings(target_kcal='40')
        snapshot=dict(name='Precise batch',recipe_id=99,servings=3,meals=['breakfast'],allergens=[],allergen_complete=True,dietary_tags=[],
            ingredients=[dict(grams='100',food=dict(name='Precise food',kcal_100g='120',protein_100g='0',carbs_100g='0',fat_100g='0',provenance={}),
                              allergens=[],allergen_complete=True,ingredient_labels=['rice'])])
        pid,ids=self.plan([snapshot])
        path=f'/nutrition/meal-plans/{pid}'
        self.assertEqual(self.post(self.a,path+'/save').status_code,302)
        self.assertEqual(self.post(self.a,path+f'/meals/{ids[0]}/log',confirm='log').status_code,302)
        with self.connect_db() as conn:
            entry=conn.execute('SELECT entry_id,grams FROM nutrition_diary_entry WHERE user_id=%s',(self.owner,)).fetchone()
        self.assertEqual(entry[1],D(100)/3)
        edit=f'/nutrition/diary/2026-11-07/entries/{entry[0]}/edit'
        self.assertIn(str(entry[1]).encode(),self.a.get(edit).data)
        response=self.post(self.a,edit,logged_on='2026-11-07',meal='breakfast',grams=str(entry[1]),food_name='Precise food',kcal_100g='120',protein_100g='0',carbs_100g='0',fat_100g='0')
        self.assertEqual(response.status_code,302)
