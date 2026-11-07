"""Pinned import, fail-closed exclusions and private reviewable AI meal drafts."""
import copy
from decimal import Decimal as D
import json
from pathlib import Path
import shutil
import httpx
from tempfile import TemporaryDirectory
from unittest.mock import patch
from flask import template_rendered
from psycopg.types.json import Jsonb

from app.services import recipe_catalogue as catalog, nutrition_ai, recipe_index, workout_ai
from app.services.meal_planner import exclusion_reasons, restrictions, totals, portion_entries
from test_plans_postgres import PostgresTestCase


class RecipeRetrievalTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        with self.connect_db() as conn:
            conn.execute('DELETE FROM recipe_catalogue')
            catalog.import_pilot(conn)

    def settings(self, **changes):
        values=dict(timezone='Europe/Sofia',allergies='',unwanted_ingredients='',dietary_preferences='')
        self.assertEqual(self.post(self.a,'/nutrition/settings',**{**values,**changes}).status_code,302)

    def choose(self, context, schema):
        rows=context['eligible_recipes']
        return json.dumps(dict(meals={meal:dict(recipe_id=next(row['recipe_id'] for row in rows if meal in row['meals']),portion='1') if any(meal in row['meals'] for row in rows) else None
            for meal in ('breakfast','lunch','dinner','snack')}))

    def generate(self, response=None, side_effect=None):
        with (patch('app.services.recipe_index.rank',side_effect=lambda rows,text,**kwargs:rows[:]) as rank,
              patch('app.services.workout_ai.nutrition',side_effect=side_effect or self.choose,return_value=response) as model):
            # Explicit response mocks replace the chooser only when supplied.
            if response is not None:model.side_effect=None
            result=self.post(self.a,'/nutrition/meal-plans/ai',planned_on='2026-11-07',meal_style='Higher protein, quick breakfasts')
        return result,rank,model

    def test_repeatable_import_dry_run_source_checksums_and_inactive_preservation(self):
        with self.connect_db() as conn:
            before=conn.execute('SELECT * FROM recipe_catalogue ORDER BY recipe_id').fetchall()
            self.assertEqual(catalog.import_pilot(conn,True)['new'],0)
            self.assertEqual(catalog.import_pilot(conn)['changed'],0)
            self.assertEqual(catalog.import_pilot(conn)['new'],0)
            self.assertEqual(conn.execute('SELECT * FROM recipe_catalogue ORDER BY recipe_id').fetchall(),before)
            conn.execute("UPDATE recipe_catalogue SET is_active=false WHERE recipe_id='unitools:birchermuesli'")
            catalog.import_pilot(conn)
            self.assertFalse(conn.execute("SELECT is_active FROM recipe_catalogue WHERE recipe_id='unitools:birchermuesli'").fetchone()[0])
        self.assertEqual(len(before),100)
        rows=catalog.load_pilot()
        self.assertEqual(sum('quick breakfast' in row['tags'] for row in rows),1)
        self.assertTrue(all(row['provenance']['license']=='CC BY-SA 4.0' for row in rows))
        self.assertTrue(all(row['ingredients'] and row['instructions'] and row['macros'] for row in rows))
        with TemporaryDirectory() as folder:
            for name in ['manifest.json','ingredient-review.json','unitools-recipes-v1.json','LICENSE']:
                shutil.copyfile(catalog.DATA_ROOT/name,Path(folder)/name)
            (Path(folder)/'LICENSE').write_text('tampered',encoding='utf-8')
            with self.assertRaises(ValueError):catalog.load_pilot(folder)
        with patch('app.services.recipe_index.index_recipes') as index:
            result=self.app.test_cli_runner().invoke(args=['import-recipes','--dry-run','--index'])
        self.assertEqual(result.exit_code,0,result.output)
        index.assert_not_called()

    def test_allergy_unknown_ingredients_and_exclusions_are_checked_before_ranking(self):
        self.settings(allergies='peanut',unwanted_ingredients='chicken')
        captured=[]
        def inspect(rows,text,**kwargs):
            captured.extend(rows)
            self.assertTrue(all(row['allergen_complete'] for row in rows))
            self.assertTrue(all('peanut' not in row['allergens'] for row in rows))
            self.assertTrue(all(not any('chicken' in child['ingredient_labels'] for child in row['ingredients']) for row in rows))
            return rows
        with patch('app.services.recipe_index.rank',side_effect=inspect),patch('app.services.workout_ai.nutrition',side_effect=self.choose):
            self.post(self.a,'/nutrition/meal-plans/ai',planned_on='2026-11-07')
        self.assertTrue(captured)
        with self.connect_db() as conn:
            rules,_=restrictions(dict(allergies='peanut',unwanted_ingredients='',dietary_preferences=''))
            allowed,excluded=catalog.eligible(conn,rules)
        self.assertTrue(any(any('Incomplete' in reason for reason in row['reasons']) for row in excluded))
        candidate=copy.deepcopy(allowed[0])
        candidate['ingredients'][0]['allergen_complete']=False
        self.assertTrue(exclusion_reasons(candidate,rules))
        candidate['ingredients'][0]['food']['name']='Mystery seasoning'
        self.assertTrue(exclusion_reasons(candidate,rules))

    def test_empty_or_unknown_restrictions_do_not_rank_or_call_provider(self):
        for change in [dict(allergies='unrecognised allergy'),dict(dietary_preferences='unknown diet')]:
            self.settings(**change)
            response,rank,model=self.generate()
            self.assertEqual(response.status_code,200)
            rank.assert_not_called();model.assert_not_called()
        self.settings(unwanted_ingredients='salt, water, olive oil, egg, milk, beef, chicken, fish, rice, flour, potato')
        with self.connect_db() as conn:conn.execute('UPDATE recipe_catalogue SET is_active=false')
        response,rank,model=self.generate()
        self.assertIn(b'No recipes satisfy',response.data)
        rank.assert_not_called();model.assert_not_called()

    def test_valid_draft_totals_use_stored_macros_edit_save_and_never_invent_grams(self):
        self.settings()
        response,_,model=self.generate()
        self.assertEqual(response.status_code,302)
        identifier=int(response.location.rsplit('/',1)[1])
        with self.connect_db() as conn:
            plan=conn.execute('SELECT status,include_catalogue FROM meal_plan WHERE plan_id=%s',(identifier,)).fetchone()
            rows=conn.execute('SELECT meal_id,recipe_snapshot,portion FROM meal_plan_meal WHERE plan_id=%s ORDER BY meal_id',(identifier,)).fetchall()
        self.assertEqual(plan,('draft',True))
        self.assertEqual(len(rows),3)  # No unneeded snack when all targets are unset.
        for _,recipe,portion in rows:
            entry=portion_entries(recipe,portion)
            self.assertNotIn('grams',entry[0])
            self.assertEqual(totals(entry)['protein'],D(recipe['macros']['protein'])*portion)
        context=model.call_args.args[0]
        self.assertNotIn('user_id',context)
        self.assertNotIn('password',json.dumps(context))
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{identifier}/meals/{rows[0][0]}/edit',recipe_id='snapshot',portion='1.25').status_code,302)
        page=self.a.get(response.location)
        self.assertIn(b'CC BY-SA 4.0',page.data)
        self.assertIn(b'Original batch:',page.data)
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{identifier}/save').status_code,302)
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{identifier}/meals/{rows[0][0]}/log',confirm='log').status_code,409)
        with self.connect_db() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM nutrition_diary_entry').fetchone()[0],0)

    def test_fabricated_ids_portions_macros_wrong_slots_and_duplicate_meals_rejected(self):
        with self.connect_db() as conn:rows,_=catalog.eligible(conn,dict(allergies=[],unwanted=[],diets=[]))
        context=dict(eligible_recipes=rows)
        good=json.loads(self.choose(context,{}))
        variants=[]
        for change in [dict(recipe_id='private:999'),dict(portion='NaN'),dict(portion='2.25'),dict(portion=True),dict(kcal=1),dict(meal='extra')]:
            bad=copy.deepcopy(good);bad['meals']['breakfast'].update(change);variants.append(bad)
        bad=copy.deepcopy(good);bad['meals']['extra']=bad['meals'].pop('lunch');variants.append(bad)
        bad=copy.deepcopy(good);bad['meals']['breakfast']['recipe_id']=next(row['recipe_id'] for row in rows if 'breakfast' not in row['meals']);variants.append(bad)
        for bad in variants:
            response,_,_=self.generate(json.dumps(bad))
            self.assertEqual(response.status_code,200)
        with self.connect_db() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM meal_plan').fetchone()[0],0)

    def test_provider_failure_chroma_failure_and_changed_preferences_keep_manual_working(self):
        response,_,_=self.generate(side_effect=workout_ai.InferenceError('synthetic-key-never-print'))
        self.assertIn(b'unavailable',response.data)
        self.assertNotIn(b'synthetic-key-never-print',response.data)
        with patch('app.services.recipe_index.rank',side_effect=httpx.ConnectError('private network details')):
            outage=self.post(self.a,'/nutrition/meal-plans/ai',planned_on='2026-11-07')
        self.assertIn(b'unavailable',outage.data)
        self.assertNotIn(b'private network details',outage.data)
        def changed(context,schema):
            self.settings(allergies='egg')
            return self.choose(context,schema)
        response,_,_=self.generate(side_effect=changed)
        self.assertIn(b'changed during generation',response.data)
        with self.connect_db() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM meal_plan').fetchone()[0],0)
        self.assertEqual(self.a.get('/nutrition/meal-plans/new').status_code,200)

    def test_current_allergies_and_inactive_sources_block_save_and_ownership(self):
        self.settings()
        response,_,_=self.generate()
        identifier=int(response.location.rsplit('/',1)[1])
        self.assertEqual(self.b.get(response.location).status_code,404)
        self.assertEqual(self.post(self.b,f'/nutrition/meal-plans/{identifier}/save').status_code,404)
        with self.connect_db() as conn:conn.execute('UPDATE recipe_catalogue SET is_active=false')
        self.assertEqual(self.post(self.a,f'/nutrition/meal-plans/{identifier}/save').status_code,409)
        self.assertEqual(self.a.post('/nutrition/meal-plans/ai').status_code,400)
        self.assertEqual(self.app.test_client().get('/nutrition/meal-plans/ai').status_code,302)

    def test_index_documents_are_public_only_and_rank_query_is_eligible_scoped(self):
        rows=catalog.load_pilot()
        doc=recipe_index.document(rows[0])
        self.assertIn(rows[0]['description'],doc)
        self.assertNotIn('user_id',doc)
        self.assertEqual(recipe_index.embedding(doc),recipe_index.embedding(doc))
        class Response:
            def __init__(self,value):self.value=value
            def raise_for_status(self):pass
            def json(self):return self.value
        with patch('app.services.recipe_index.httpx.Client') as client:
            connection=client.return_value.__enter__.return_value
            connection.get.return_value=Response(dict(id='synthetic-collection',metadata=recipe_index.METADATA))
            connection.post.return_value=Response(dict(ids=[['private:999',rows[0]['recipe_id']]]))
            ranked=recipe_index.rank(rows[:1],'private query text')
        self.assertEqual([row['recipe_id'] for row in ranked],[rows[0]['recipe_id']])
        payload=connection.post.call_args.kwargs['json']
        self.assertEqual(payload['where'],{'recipe_id':{'$in':[rows[0]['recipe_id']]}})
        self.assertNotIn('private query text',json.dumps(payload))

    def test_repeat_import_preserves_existing_plan_and_diary_snapshots_and_retires_legacy(self):
        self.settings()
        response,_,_=self.generate()
        identifier=int(response.location.rsplit('/',1)[1])
        with self.connect_db() as conn:
            # Represent an existing pilot reference without copying any account data.
            row=conn.execute('SELECT recipe_snapshot FROM meal_plan_meal WHERE plan_id=%s LIMIT 1',(identifier,)).fetchone()[0]
            legacy=copy.deepcopy(row);legacy['recipe_id']='unitools:menemen'
            conn.execute('UPDATE meal_plan_meal SET recipe_snapshot=%s WHERE plan_id=%s',(Jsonb(legacy),identifier))
            conn.execute("INSERT INTO recipe_catalogue(recipe_id,source,source_id,name,description,servings,meals,tags,ingredients,instructions,macros,provenance,content_sha256) SELECT 'unitools:menemen',source,'menemen',name,description,servings,meals,tags,ingredients,instructions,macros,provenance,content_sha256 FROM recipe_catalogue LIMIT 1")
            conn.execute("INSERT INTO nutrition_diary_entry(user_id,logged_on,meal,food_name,grams,kcal_100g,protein_100g,carbs_100g,fat_100g) VALUES (%s,'2026-11-07','breakfast','Historical sample',33.333333,100,10,12,2)",(self.owner,))
            before_plan=conn.execute('SELECT * FROM meal_plan_meal WHERE plan_id=%s ORDER BY meal_id',(identifier,)).fetchall()
            before_diary=conn.execute('SELECT * FROM nutrition_diary_entry').fetchall()
            catalog.import_pilot(conn);catalog.import_pilot(conn)
            self.assertEqual(conn.execute('SELECT * FROM meal_plan_meal WHERE plan_id=%s ORDER BY meal_id',(identifier,)).fetchall(),before_plan)
            self.assertEqual(conn.execute('SELECT * FROM nutrition_diary_entry').fetchall(),before_diary)
            self.assertFalse(conn.execute("SELECT is_active FROM recipe_catalogue WHERE recipe_id='unitools:menemen'").fetchone()[0])
            self.assertEqual(conn.execute("SELECT count(*) FROM recipe_catalogue WHERE source='unitools'").fetchone()[0],101)
