"""Server-ranked saved meal swaps, signed previews and immutable diary history."""
from concurrent.futures import ThreadPoolExecutor
import copy
from decimal import Decimal as D
import json
from unittest.mock import patch

from flask import template_rendered
from itsdangerous import SignatureExpired
from psycopg.types.json import Jsonb

from app.services import meal_swaps, recipe_catalogue, workout_ai
from test_plans_postgres import PostgresTestCase


class MealSwapTests(PostgresTestCase):
    def recipe(self,key,kcal=400,protein=20,minutes=60,*,complete=True,allergens=None):
        ingredients=[dict(source_id='rice',quantity='200',unit='g',scaling='linear',note='',
            food=dict(name='Plain rice',source_allergens=allergens or []),allergens=allergens or [],
            allergen_complete=complete,ingredient_labels=['rice'])]
        values=dict(kcal=None if kcal is None else str(kcal),protein=None if protein is None else str(protein),carbs='40',fat='10')
        provenance=dict(commit='synthetic-revision',attribution='Synthetic source',homepage='https://example.invalid',license_url='https://example.invalid')
        with self.connect_db() as conn:
            conn.execute("INSERT INTO recipe_catalogue(recipe_id,source,source_id,name,description,servings,meals,tags,ingredients,instructions,macros,provenance,content_sha256,prep_minutes,cook_minutes) "
                "VALUES (%s,'swap-test',%s,%s,'Synthetic recipe',4,ARRAY['lunch'],ARRAY['sample'],%s,%s,%s,%s,'synthetic',%s,0)",
                ('unitools:swap-'+key,key,'Sample '+key,Jsonb(ingredients),Jsonb([dict(text='Cook the rice.',minutes=10)]),Jsonb(values),Jsonb(provenance),minutes))
            from app.routes.plans import query
            return recipe_catalogue.snapshot(query(conn,'SELECT * FROM recipe_catalogue WHERE recipe_id=%s',('unitools:swap-'+key,),one=True))

    def setUp(self):
        super().setUp()
        with self.connect_db() as conn:conn.execute('DELETE FROM recipe_catalogue')
        self.current=self.recipe('current')
        self.a_recipe=self.recipe('a',300,30,20)
        self.b_recipe=self.recipe('b',500,40,80)
        self.c_recipe=self.recipe('c',450,25,40)
        self.d_recipe=self.recipe('d',1200,80,10)
        with self.connect_db() as conn:
            self.plan=conn.execute("INSERT INTO meal_plan(user_id,planned_on,status,saved_at,targets,restrictions,include_catalogue) VALUES (%s,'2026-11-07','saved',now(),%s,%s,true) RETURNING plan_id",
                (self.owner,Jsonb(dict(kcal=None,protein=None,carbs=None,fat=None)),Jsonb(dict(allergies=[],unwanted=[],diets=[])))).fetchone()[0]
            self.meal=conn.execute("INSERT INTO meal_plan_meal(plan_id,user_id,meal,portion,recipe_snapshot) VALUES (%s,%s,'lunch',1.5,%s) RETURNING meal_id",
                (self.plan,self.owner,Jsonb(self.current))).fetchone()[0]
            self.other_meal=conn.execute("INSERT INTO meal_plan_meal(plan_id,user_id,meal,portion,recipe_snapshot) VALUES (%s,%s,'dinner',1,%s) RETURNING meal_id",
                (self.plan,self.owner,Jsonb(self.current))).fetchone()[0]
        self.path=f'/nutrition/meal-plans/{self.plan}/meals/{self.meal}/swap'
        self.replace=f'/nutrition/meal-plans/{self.plan}/meals/{self.meal}/replace'

    def preview(self,goal='similar',explain=False):
        contexts=[]
        def capture(sender,template,context,**extra):contexts.append(context)
        with template_rendered.connected_to(capture,self.app):
            response=self.post(self.a,self.path,goal=goal) if explain else self.a.get(self.path+'?goal='+goal)
        self.assertEqual(response.status_code,200)
        return response,contexts[-1]

    def confirm(self,option,client=None):
        return self.post(client or self.a,self.replace,preview=option['token'],confirm='replace')

    def test_rank_scaled_portions_tolerance_distinct_recipes_and_all_macro_differences(self):
        response,data=self.preview()
        self.assertEqual(data['baseline']['totals']['kcal'],D(600))
        self.assertEqual(data['baseline']['tolerance'],D(90))
        options=data['options']
        self.assertEqual(len(options),3)
        self.assertEqual(len({row['recipe']['recipe_id'] for row in options}),3)
        self.assertNotIn(self.current['recipe_id'],[row['recipe']['recipe_id'] for row in options])
        first=options[0]
        self.assertEqual(first['recipe']['recipe_id'],self.a_recipe['recipe_id'])
        self.assertEqual(first['portion'],D(2))
        self.assertEqual(first['totals']['protein'],D(60))
        self.assertEqual(first['difference']['protein'],D(30))
        self.assertIn(b'Ingredients:',response.data)

    def test_protein_requires_strict_gain_and_quicker_uses_batch_time_not_scaled_time(self):
        _,data=self.preview('protein')
        self.assertTrue(all(option['difference']['protein']>0 for option in data['options']))
        self.assertTrue(all(abs(option['difference']['kcal'])<=90 for option in data['options']))
        _,data=self.preview('quicker')
        self.assertEqual([row['minutes'] for row in data['options']],[10,20,40])
        self.assertEqual(data['options'][0]['portion'],D('.5'))
        self.assertEqual(data['options'][0]['minutes'],10)  # Original batch, not five minutes.
        with self.connect_db() as conn:conn.execute("UPDATE recipe_catalogue SET prep_minutes=0 WHERE recipe_id=%s",(self.d_recipe['recipe_id'],))
        self.assertEqual(self.preview('quicker')[1]['options'][0]['minutes'],0)

    def test_exclusions_and_unknown_ingredient_metadata_applied_before_alternatives(self):
        with self.connect_db() as conn:
            ingredients=copy.deepcopy(self.a_recipe['ingredients']);ingredients[0]['allergen_complete']=False
            conn.execute('UPDATE recipe_catalogue SET ingredients=%s WHERE recipe_id=%s',(Jsonb(ingredients),self.a_recipe['recipe_id']))
            ingredients=copy.deepcopy(self.b_recipe['ingredients']);ingredients[0]['allergens']=['peanut']
            conn.execute('UPDATE recipe_catalogue SET ingredients=%s WHERE recipe_id=%s',(Jsonb(ingredients),self.b_recipe['recipe_id']))
        self.post(self.a,'/nutrition/settings',timezone='Europe/Sofia',allergies='peanut')
        _,data=self.preview()
        ids=[row['recipe']['recipe_id'] for row in data['options']]
        self.assertNotIn(self.a_recipe['recipe_id'],ids)
        self.assertNotIn(self.b_recipe['recipe_id'],ids)
        self.assertTrue(ids)
        self.post(self.a,'/nutrition/settings',timezone='Europe/Sofia',unwanted_ingredients='rice')
        response,data=self.preview()
        self.assertEqual(data['options'],[])
        self.assertIn(b'Try another comparison',response.data)

    def test_missing_comparison_values_and_legacy_preparation_are_not_guessed(self):
        with self.connect_db() as conn:
            conn.execute('UPDATE recipe_catalogue SET prep_minutes=NULL WHERE recipe_id=%s',(self.a_recipe['recipe_id'],))
            conn.execute("UPDATE recipe_catalogue SET macros=jsonb_set(macros,'{protein}','null') WHERE recipe_id=%s",(self.b_recipe['recipe_id'],))
        _,data=self.preview('quicker')
        self.assertNotIn(self.a_recipe['recipe_id'],[row['recipe']['recipe_id'] for row in data['options']])
        _,data=self.preview('protein')
        self.assertNotIn(self.b_recipe['recipe_id'],[row['recipe']['recipe_id'] for row in data['options']])
        # Legacy snapshot can use time from the same pinned source revision.
        with self.connect_db() as conn:
            legacy=copy.deepcopy(self.current);legacy.pop('prep_minutes');legacy.pop('cook_minutes')
            conn.execute('UPDATE meal_plan_meal SET recipe_snapshot=%s WHERE meal_id=%s',(Jsonb(legacy),self.meal))
        self.assertEqual(self.preview('quicker')[1]['baseline']['minutes'],60)
        legacy['provenance']['commit']='different-revision'
        with self.connect_db() as conn:conn.execute('UPDATE meal_plan_meal SET recipe_snapshot=%s WHERE meal_id=%s',(Jsonb(legacy),self.meal))
        response,data=self.preview('quicker')
        self.assertEqual(data['options'],[])
        self.assertIn(b'Preparation time is unavailable',response.data)

    def test_unknown_current_nutrition_and_minimum_50_kcal_tolerance(self):
        with self.connect_db() as conn:
            current=copy.deepcopy(self.current);current['macros']['kcal']='100'
            conn.execute('UPDATE meal_plan_meal SET recipe_snapshot=%s,portion=.5 WHERE meal_id=%s',(Jsonb(current),self.meal))
        self.assertEqual(self.preview()[1]['baseline']['tolerance'],D(50))
        current['macros']['kcal']=None
        with self.connect_db() as conn:conn.execute('UPDATE meal_plan_meal SET recipe_snapshot=%s WHERE meal_id=%s',(Jsonb(current),self.meal))
        response,data=self.preview()
        self.assertEqual(data['options'],[])
        self.assertIn(b'calories are unknown',response.data)

    def test_preview_does_not_infer_or_write_and_confirmation_changes_only_selected_meal(self):
        with self.connect_db() as conn:
            before=conn.execute('SELECT * FROM meal_plan_meal ORDER BY meal_id').fetchall()
            plan=conn.execute('SELECT * FROM meal_plan').fetchall()
        with patch('app.services.workout_ai.explain_meal_swaps') as model:
            _,data=self.preview()
        model.assert_not_called()
        with self.connect_db() as conn:self.assertEqual(conn.execute('SELECT * FROM meal_plan_meal ORDER BY meal_id').fetchall(),before)
        self.assertEqual(self.post(self.a,self.replace,preview=data['options'][0]['token']).status_code,400)
        self.assertEqual(self.confirm(data['options'][0]).status_code,302)
        with self.connect_db() as conn:
            after=conn.execute('SELECT * FROM meal_plan_meal ORDER BY meal_id').fetchall()
            self.assertEqual(after[1],before[1])
            self.assertEqual(conn.execute('SELECT * FROM meal_plan').fetchall(),plan)
            self.assertEqual(conn.execute('SELECT version FROM meal_plan_meal WHERE meal_id=%s',(self.meal,)).fetchone()[0],2)
        contexts=[]
        def capture(sender,template,context,**extra):contexts.append(context)
        with template_rendered.connected_to(capture,self.app):
            page=self.a.get(f'/nutrition/meal-plans/{self.plan}')
        self.assertIn(b'Planned meal replaced',page.data)
        self.assertEqual(contexts[-1]['total']['protein'],D(80))

    def test_account_isolation_wrong_meal_csrf_and_saved_only(self):
        self.assertEqual(self.b.get(self.path).status_code,404)
        self.assertEqual(self.post(self.b,self.replace,confirm='replace').status_code,404)
        self.assertEqual(self.a.post(self.replace).status_code,400)
        self.assertEqual(self.app.test_client().get(self.path).status_code,302)
        _,data=self.preview()
        other=f'/nutrition/meal-plans/{self.plan}/meals/{self.other_meal}/replace'
        self.assertEqual(self.post(self.a,other,preview=data['options'][0]['token'],confirm='replace').status_code,400)
        self.assertEqual(self.a.get(self.path+'?goal=unsupported').status_code,400)
        with self.connect_db() as conn:conn.execute("UPDATE meal_plan SET status='draft',saved_at=NULL WHERE plan_id=%s",(self.plan,))
        self.assertEqual(self.a.get(self.path).status_code,409)
        self.assertEqual(self.confirm(data['options'][0]).status_code,409)

    def test_double_confirmation_and_stale_versions_have_same_conflict_behavior(self):
        _,data=self.preview();option=data['options'][0]
        self.assertEqual(self.confirm(option).status_code,302)
        self.assertEqual(self.confirm(option).status_code,409)
        with self.connect_db() as conn:self.assertEqual(conn.execute('SELECT version FROM meal_plan_meal WHERE meal_id=%s',(self.meal,)).fetchone()[0],2)
        _,fresh=self.preview()
        with self.connect_db() as conn:conn.execute('UPDATE meal_plan_meal SET version=version+1 WHERE meal_id=%s',(self.meal,))
        self.assertEqual(self.confirm(fresh['options'][0]).status_code,409)

    def test_concurrent_confirmations_apply_once(self):
        _,data=self.preview();option=data['options'][0]
        second=self.app.test_client()
        with self.a.session_transaction() as state:session=dict(state)
        with second.session_transaction() as state:state.update(session)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(lambda client:self.confirm(option,client).status_code,[self.a,second]))
        self.assertEqual(sorted(outcomes),[302,409])
        with self.connect_db() as conn:self.assertEqual(conn.execute('SELECT version FROM meal_plan_meal WHERE meal_id=%s',(self.meal,)).fetchone()[0],2)

    def test_source_changes_deactivation_allergy_changes_and_portions_revalidated(self):
        _,data=self.preview();option=data['options'][0]
        with self.connect_db() as conn:conn.execute('UPDATE recipe_catalogue SET is_active=false WHERE recipe_id=%s',(option['recipe']['recipe_id'],))
        self.assertEqual(self.confirm(option).status_code,409)
        with self.connect_db() as conn:conn.execute('UPDATE recipe_catalogue SET is_active=true WHERE recipe_id=%s',(option['recipe']['recipe_id'],))
        self.post(self.a,'/nutrition/settings',timezone='Europe/Sofia',unwanted_ingredients='rice')
        self.assertEqual(self.confirm(option).status_code,409)
        self.post(self.a,'/nutrition/settings',timezone='Europe/Sofia')
        with self.connect_db() as conn:conn.execute("UPDATE recipe_catalogue SET macros=jsonb_set(macros,'{protein}','\"31\"') WHERE recipe_id=%s",(option['recipe']['recipe_id'],))
        self.assertEqual(self.confirm(option).status_code,409)
        with self.app.app_context():
            value=meal_swaps.serializer().loads(option['token']);value['portion']='2.25'
            invalid=meal_swaps.serializer().dumps(value)
        self.assertEqual(self.post(self.a,self.replace,preview=invalid,confirm='replace').status_code,400)
        self.assertEqual(self.post(self.a,self.replace,preview=option['token']+'tamper',confirm='replace').status_code,400)
        with patch('app.services.meal_swaps.serializer') as factory:
            factory.return_value.loads.side_effect=SignatureExpired('expired')
            self.assertEqual(self.confirm(option).status_code,409)

    def test_diary_entries_and_logged_marker_survive_swap_unchanged(self):
        with self.connect_db() as conn:
            conn.execute('UPDATE meal_plan_meal SET logged_at=now() WHERE meal_id=%s',(self.meal,))
            conn.execute("INSERT INTO nutrition_diary_entry(user_id,logged_on,meal,food_name,grams,kcal_100g,protein_100g,plan_meal_id,plan_ingredient_index) VALUES (%s,'2026-11-07','lunch','Historical consumed meal',100,456.78,12.34,%s,0)",(self.owner,self.meal))
            before=conn.execute('SELECT * FROM nutrition_diary_entry').fetchall()
            marker=conn.execute('SELECT logged_at FROM meal_plan_meal WHERE meal_id=%s',(self.meal,)).fetchone()[0]
        _,data=self.preview();self.assertEqual(self.confirm(data['options'][0]).status_code,302)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT * FROM nutrition_diary_entry').fetchall(),before)
            self.assertEqual(conn.execute('SELECT logged_at FROM meal_plan_meal WHERE meal_id=%s',(self.meal,)).fetchone()[0],marker)
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{self.plan}/meals/{self.meal}/log',confirm='log').status_code,409)

    def test_optional_coach_failure_or_fabrications_never_remove_alternatives(self):
        with patch('app.services.workout_ai.explain_meal_swaps',side_effect=workout_ai.InferenceError('private-key-details')):
            response,data=self.preview(explain=True)
        self.assertEqual(len(data['options']),3)
        self.assertIn(b'alternatives are ready to use',response.data)
        self.assertNotIn(b'private-key-details',response.data)
        bad=json.dumps(dict(explanations=[dict(recipe_id='fabricated',reason_id='protein_gain')]))
        with patch('app.services.workout_ai.explain_meal_swaps',return_value=bad):
            self.assertEqual(self.preview(explain=True)[1]['explanations'],{})
        def valid(context,schema):
            return json.dumps(dict(explanations=[dict(recipe_id=row['recipe_id'],reason_id=row['reason_ids'][0]) for row in context['alternatives']]))
        with patch('app.services.workout_ai.explain_meal_swaps',side_effect=valid):
            response,data=self.preview(explain=True)
        self.assertEqual(len(data['explanations']),3)
        self.assertIn(b'calorie comparison range',response.data)
