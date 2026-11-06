"""Private nutrition CRUD, snapshots, precision, migrations and timezone behavior."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
import time
from unittest.mock import patch

from flask import template_rendered
from psycopg.errors import ForeignKeyViolation

from app.cli import upgrade
from test_plans_postgres import PostgresTestCase

D = Decimal
DAY = '2026-01-02'


class NutritionIntegrationTests(PostgresTestCase):
    def save_settings(self, client=None, **overrides):
        values=dict(allergies='Peanuts', unwanted_ingredients='Olives', dietary_preferences='Vegetarian',
                    timezone='Europe/Sofia', target_kcal='2000', target_protein='100', target_carbs='200', target_fat='50')
        return self.post(client or self.a,'/nutrition/settings',**{**values,**overrides})

    def create_food(self, client=None, **overrides):
        values=dict(name='My food',kcal_100g='200',protein_100g='10',carbs_100g='25',fat_100g='6')
        return self.post(client or self.a,'/nutrition/foods/new',**{**values,**overrides})

    def add_entry(self, client=None, day=DAY, **overrides):
        values=dict(logged_on=day,meal='breakfast',grams='100',food_name='Manual food',
                    kcal_100g='200',protein_100g='10',carbs_100g='25',fat_100g='6')
        return self.post(client or self.a,f'/nutrition/diary/{day}/entries/new',**{**values,**overrides})

    def foods(self):
        with self.connect_db() as conn:
            return conn.execute('SELECT food_id,user_id,name,kcal_100g,protein_100g,carbs_100g,fat_100g FROM nutrition_food ORDER BY food_id').fetchall()

    def entries(self):
        with self.connect_db() as conn:
            return conn.execute('SELECT entry_id,user_id,logged_on,meal,food_id,food_name,grams,'
                                'kcal_100g,protein_100g,carbs_100g,fat_100g FROM nutrition_diary_entry ORDER BY entry_id').fetchall()

    def settings(self, owner=None):
        with self.connect_db() as conn:
            return conn.execute('SELECT allergies,unwanted_ingredients,dietary_preferences,timezone,'
                                'target_kcal,target_protein,target_carbs,target_fat FROM nutrition_settings WHERE user_id=%s',
                                (owner or self.owner,)).fetchone()

    def diary(self, day=None, client=None):
        captured=[]
        def record(sender,template,context,**extra):
            captured.append(context)
        with template_rendered.connected_to(record,self.app):
            response=(client or self.a).get('/nutrition'+('?date='+day if day else ''))
        self.assertEqual(response.status_code,200)
        return response,captured[-1]

    def test_settings_defaults_are_unset_and_get_does_not_write(self):
        response,context=self.diary(DAY)
        self.assertEqual(context['settings']['timezone'],'Europe/Sofia')
        self.assertIsNone(context['totals']['kcal']['target'])
        self.assertEqual(context['totals']['kcal']['total'],D('0'))
        self.assertIn(b'No breakfast entries',response.data)
        self.assertIn(b'Not set',response.data)
        self.assertIsNone(self.settings())
        self.assertEqual(self.a.get('/nutrition/settings').status_code,200)
        self.assertIsNone(self.settings())
        self.assertIn(b'/nutrition',self.a.get('/dashboard').data)

    def test_preferences_are_separate_private_and_targets_never_reconciled(self):
        self.assertEqual(self.save_settings().status_code,302)
        self.assertEqual(self.settings(),('Peanuts','Olives','Vegetarian','Europe/Sofia',D('2000'),D('100'),D('200'),D('50')))
        page=self.a.get('/nutrition/settings').data
        self.assertIn(b'350 kcal higher',page)
        self.assertIn(b'1650 kcal',page)
        self.assertIn(b'guarantee',page)
        self.assertNotIn(b'Peanuts',self.b.get('/nutrition/settings').data)
        original=self.settings()
        self.assertEqual(self.save_settings(self.b,user_id=self.owner,allergies='Milk').status_code,302)
        self.assertEqual(self.settings(),original)
        self.assertEqual(self.save_settings(target_kcal='',target_protein='',target_carbs='',target_fat='',allergies='',
                                            unwanted_ingredients='',dietary_preferences='').status_code,302)
        self.assertEqual(self.settings(),('','','','Europe/Sofia',None,None,None,None))

    def test_settings_validation_preserves_existing_values(self):
        self.save_settings(); original=self.settings()
        invalid=[dict(target_kcal=value) for value in ['NaN','sNaN','Infinity','-1','20000.01','1e3','1.001','9'*100]]
        invalid += [dict(target_protein='2000.01'),dict(target_carbs='-1'),dict(target_fat='NaN'),
                    dict(timezone='Europe/Unknown'),dict(timezone='../Europe/Sofia'),dict(timezone='+02:00'),
                    dict(allergies='x'*2001),dict(unwanted_ingredients='x'*2001),dict(dietary_preferences='x'*2001)]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                response=self.save_settings(**overrides)
                self.assertEqual(response.status_code,200)
                self.assertIn(b'role="alert"',response.data)
                self.assertEqual(self.settings(),original)
        self.assertEqual(self.save_settings(target_kcal=20000,target_protein=2000,target_carbs=0,target_fat=0,timezone='UTC').status_code,302)

    def test_saved_food_crud_and_unknowns_are_preserved_as_null(self):
        self.assertIn(b'Your food library starts here',self.a.get('/nutrition/foods').data)
        self.assertEqual(self.create_food(name='Unknown label',kcal_100g='',protein_100g=0,carbs_100g='',fat_100g='').status_code,302)
        food=self.foods()[0]
        self.assertEqual(food[2:],('Unknown label',None,D('0'),None,None))
        self.assertIn(b'Unknown',self.a.get('/nutrition/foods').data)
        edit=f'/nutrition/foods/{food[0]}/edit'
        self.assertEqual(self.a.get(edit).status_code,200)
        self.assertEqual(self.post(self.a,edit,name='Updated food',kcal_100g=1000,protein_100g=100,carbs_100g=100,fat_100g=100).status_code,302)
        self.assertEqual(self.foods()[0][2:],('Updated food',D('1000'),D('100'),D('100'),D('100')))

    def test_food_validation_rejects_nonfinite_negative_and_excess_precision(self):
        self.create_food(); original=self.foods(); edit=f'/nutrition/foods/{original[0][0]}/edit'
        valid=dict(name='My food',kcal_100g='200',protein_100g='10',carbs_100g='25',fat_100g='6')
        invalid=[dict(kcal_100g=value) for value in ['NaN','nan','sNaN','Infinity','-1','1000.01','1.001','1e2','1_00','9'*100]]
        invalid += [dict(name=''),dict(name='x'*101),dict(protein_100g='100.01'),dict(carbs_100g='-0.01'),dict(fat_100g='Infinity')]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                self.assertEqual(self.create_food(**overrides).status_code,200)
                self.assertEqual(self.post(self.a,edit,**{**valid,**overrides}).status_code,200)
                self.assertEqual(self.foods(),original)

    def test_manual_diary_crud_meals_date_move_and_confirmation(self):
        for meal in ['breakfast','lunch','dinner','snack']:
            self.assertEqual(self.add_entry(meal=meal,save_food='yes' if meal=='breakfast' else '').status_code,302)
        self.assertEqual(len(self.entries()),4)
        self.assertEqual(len(self.foods()),1)
        first=self.entries()[0]
        self.assertEqual(first[1:7],(self.owner,date(2026,1,2),'breakfast',self.foods()[0][0],'Manual food',D('100')))
        response,context=self.diary(DAY)
        self.assertEqual(context['totals']['kcal']['total'],D('800'))
        edit=f'/nutrition/diary/{DAY}/entries/{first[0]}/edit'
        self.assertEqual(self.a.get(edit).status_code,200)
        self.assertEqual(self.post(self.a,edit,logged_on='2026-01-03',meal='snack',grams='0.01',food_name='Corrected entry',
                                   kcal_100g='200',protein_100g='10',carbs_100g='25',fat_100g='6').status_code,302)
        self.assertEqual(self.entries()[0][2:4],(date(2026,1,3),'snack'))
        self.assertEqual(self.entries()[0][6],D('0.01'))
        delete=f'/nutrition/diary/2026-01-03/entries/{first[0]}/delete'
        self.assertEqual(self.a.get(delete).status_code,200)
        self.assertEqual(len(self.entries()),4)
        self.assertEqual(self.post(self.a,delete).status_code,400)
        self.assertEqual(self.post(self.a,delete,confirm='no').status_code,400)
        self.assertEqual(self.post(self.a,delete,confirm='delete').status_code,302)
        self.assertEqual(len(self.entries()),3)
        self.assertEqual(len(self.foods()),1)

    def test_saved_food_edits_never_refresh_old_snapshots(self):
        self.create_food(name='Original name',kcal_100g='100')
        food=self.foods()[0]; log=f'/nutrition/foods/{food[0]}/log?date={DAY}'
        self.assertEqual(self.a.get(log).status_code,200)
        self.assertEqual(self.post(self.a,log,logged_on=DAY,meal='lunch',grams='125.55').status_code,302)
        original=self.entries()[0]
        self.assertEqual(self.post(self.a,f'/nutrition/foods/{food[0]}/edit',name='New name',kcal_100g='500',
                                   protein_100g='20',carbs_100g='50',fat_100g='12').status_code,302)
        self.assertEqual(self.entries()[0],original)
        self.post(self.a,log,logged_on=DAY,meal='dinner',grams=100)
        self.assertEqual(self.entries()[1][5], 'New name')
        self.assertEqual(self.entries()[1][7],D('500'))
        edit=f'/nutrition/diary/{DAY}/entries/{original[0]}/edit'
        self.assertIn(b'Original name',self.a.get(edit).data)
        self.post(self.a,edit,logged_on=DAY,meal='lunch',grams=200,food_name='Original name',kcal_100g=100,
                  protein_100g=10,carbs_100g=25,fat_100g=6,food_id=999999)
        self.assertEqual(self.entries()[0][7],D('100'))
        self.assertEqual(self.entries()[0][4],food[0])

    def test_partial_totals_and_over_target_are_explicit(self):
        self.save_settings(target_kcal=150,target_protein=100,target_carbs=100,target_fat=100)
        self.add_entry(kcal_100g=200,protein_100g=0,carbs_100g='',fat_100g=0)
        self.add_entry(kcal_100g='',protein_100g=0,carbs_100g='',fat_100g=0)
        response,context=self.diary(DAY)
        self.assertIsNone(context['totals']['kcal']['total'])
        self.assertEqual(context['totals']['kcal']['subtotal'],D('200'))
        self.assertIsNone(context['totals']['kcal']['remaining'])
        self.assertIn(b'At least 50 kcal over target',response.data)
        self.assertIn(b'Incomplete',response.data)
        self.assertIn(b'Remaining unknown',response.data)
        self.assertEqual(context['totals']['protein']['total'],D('0'))
        self.assertEqual(context['totals']['carbs']['known_count'],0)
        self.assertIsNone(context['totals']['carbs']['total'])

    def test_daily_calculation_rounds_after_summing_and_small_overages_stay_visible(self):
        self.save_settings(target_kcal=0)
        for _ in range(3):
            self.add_entry(grams='33.33',kcal_100g='0.01')
        response,context=self.diary(DAY)
        self.assertEqual(context['totals']['kcal']['total'],D('0.009999'))
        self.assertIn(b'0 kcal over target',response.data)
        self.assertEqual(context['totals']['kcal']['over'],D('0.009999'))

    def test_ownership_all_food_and_entry_paths_and_mismatched_date_ids(self):
        self.create_food(name='Alice private saved food'); food_id=self.foods()[0][0]
        self.add_entry(food_name='Alice private diary food'); entry_id=self.entries()[0][0]
        valid=dict(logged_on=DAY,meal='lunch',grams=100,food_name='Stolen',kcal_100g=200)
        paths=[f'/nutrition/foods/{food_id}/edit',f'/nutrition/foods/{food_id}/log',
               f'/nutrition/diary/{DAY}/entries/{entry_id}/edit',f'/nutrition/diary/{DAY}/entries/{entry_id}/delete']
        original=self.entries(); foods=self.foods()
        for path in paths:
            self.assertEqual(self.b.get(path).status_code,404,path)
            self.assertEqual(self.post(self.b,path,confirm='delete',user_id=self.owner,**valid).status_code,404,path)
        for action in ['edit','delete']:
            wrong=f'/nutrition/diary/2026-01-03/entries/{entry_id}/{action}'
            self.assertEqual(self.a.get(wrong).status_code,404)
            self.assertEqual(self.post(self.a,wrong,confirm='delete',**valid).status_code,404)
        self.assertNotIn(b'Alice private diary food',self.diary(DAY,self.b)[0].data)
        self.assertNotIn(b'Alice private saved food',self.b.get('/nutrition/foods').data)
        self.assertEqual(self.entries(),original); self.assertEqual(self.foods(),foods)
        self.assertEqual(self.add_entry(self.b,user_id=self.owner,food_id=food_id).status_code,302)
        self.assertNotEqual(self.entries()[1][1],self.owner)
        self.assertIsNone(self.entries()[1][4])

    def test_authentication_csrf_post_only_and_cache_headers(self):
        self.create_food(); self.add_entry()
        food_id=self.foods()[0][0]; entry_id=self.entries()[0][0]
        mutations=['/nutrition/settings','/nutrition/foods/new',f'/nutrition/foods/{food_id}/edit',
                   f'/nutrition/foods/{food_id}/log',f'/nutrition/diary/{DAY}/entries/new',
                   f'/nutrition/diary/{DAY}/entries/{entry_id}/edit',f'/nutrition/diary/{DAY}/entries/{entry_id}/delete']
        anon=self.app.test_client()
        for path in ['/nutrition','/nutrition/foods',*mutations]:
            self.assertTrue(anon.get(path).location.endswith('/login'),path)
        before=self.entries(); foods=self.foods()
        for path in mutations:
            self.assertTrue(self.post(anon,path).location.endswith('/login'))
            self.assertEqual(self.a.post(path).status_code,400,path)
            self.assertEqual(self.a.post(path,data={'csrf_token':'wrong'}).status_code,400,path)
            self.assertEqual(self.a.delete(path).status_code,405,path)
        self.assertEqual(self.entries(),before); self.assertEqual(self.foods(),foods)
        self.assertEqual(self.a.get('/nutrition').headers['Cache-Control'],'no-store')

    def test_quantity_meal_date_validation_and_extreme_bounds(self):
        invalid=[dict(grams=value) for value in ['0','-1','NaN','Infinity','0.001','10000.01','1e2','']]
        invalid += [dict(meal='brunch'),dict(logged_on='2023-02-29'),dict(logged_on='2101-01-01'),
                    dict(logged_on='20260102'),dict(food_name=''),dict(food_name='x'*101),dict(fat_100g='100.01')]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                self.assertEqual(self.add_entry(save_food='yes',**overrides).status_code,200)
                self.assertEqual(self.entries(),[]); self.assertEqual(self.foods(),[])
        self.assertEqual(self.add_entry(grams='10000').status_code,302)
        for day in ['not-a-date','2023-02-29','1899-12-31']:
            self.assertEqual(self.a.get('/nutrition?date='+day).status_code,400)
            self.assertEqual(self.a.get(f'/nutrition/diary/{day}/entries/new').status_code,400)
        self.assertIsNone(self.diary('1900-01-01')[1]['previous'])
        self.assertIsNone(self.diary('2100-12-31')[1]['following'])

    def test_timezone_local_today_and_settings_changes_never_reassign_dates(self):
        instant=datetime(2026,1,1,22,30,tzinfo=timezone.utc)
        self.add_entry()
        original=self.entries()
        with patch('app.services.nutrition.utc_now',return_value=instant):
            self.assertEqual(self.diary()[1]['day'],date(2026,1,2))
            self.assertEqual(self.save_settings(timezone='America/Los_Angeles').status_code,302)
            response,context=self.diary()
            self.assertEqual(context['day'],date(2026,1,1))
            self.assertEqual(context['groups']['breakfast'],[])
            self.assertEqual(self.diary(DAY)[1]['groups']['breakfast'][0]['food_name'],'Manual food')
        self.assertEqual(self.entries(),original)

    def test_food_names_are_parameterized_and_escaped(self):
        name="<script>alert('x')</script>'; DROP TABLE exercise; --"
        self.assertEqual(self.create_food(name=name).status_code,302)
        page=self.a.get('/nutrition/foods?q=%3Cscript%3E').data
        self.assertIn(b'&lt;script&gt;',page); self.assertNotIn(b'<script>',page)
        self.assertEqual(self.add_entry(food_name=name).status_code,302)
        self.assertIn(b'&lt;script&gt;',self.diary(DAY)[0].data)
        with self.connect_db() as conn:
            self.assertGreaterEqual(conn.execute('SELECT count(*) FROM exercise').fetchone()[0],50)

    def test_manual_save_copy_is_atomic_when_entry_insert_fails(self):
        with patch('app.routes.nutrition.insert_entry',side_effect=RuntimeError('injected entry failure')):
            with self.assertRaisesRegex(RuntimeError,'injected entry failure'):
                self.add_entry(save_food='yes')
        self.assertEqual(self.foods(),[]); self.assertEqual(self.entries(),[])

    def test_database_rejects_linking_another_users_food(self):
        self.create_food(self.b); food_id=self.foods()[0][0]
        with self.assertRaises(ForeignKeyViolation):
            with self.connect_db() as conn:
                conn.execute('INSERT INTO nutrition_diary_entry(user_id,logged_on,meal,food_id,food_name,grams) '
                             'VALUES (%s,%s,%s,%s,%s,%s)',(self.owner,DAY,'snack',food_id,'Wrong owner',100))
        self.assertEqual(self.entries(),[])

    def test_repeatable_migration_retains_nutrition_records(self):
        self.save_settings(); self.add_entry(save_food='yes')
        before=(self.settings(),self.foods(),self.entries())
        with self.connect_db() as conn:
            self.assertEqual(upgrade(conn),[])
            self.assertEqual(upgrade(conn),[])
        self.assertEqual((self.settings(),self.foods(),self.entries()),before)

    def test_logging_waits_for_saved_food_edit_and_copies_a_consistent_snapshot(self):
        self.create_food(); food_id=self.foods()[0][0]
        client=self.app.test_client()
        with client.session_transaction() as state:
            state['user_id']=self.owner; state['csrf_token']='snapshot-test'
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.connect_db() as writer:
                writer.execute('UPDATE nutrition_food SET name=%s,kcal_100g=%s WHERE food_id=%s',('Changed together',500,food_id))
                future=pool.submit(self.post,client,f'/nutrition/foods/{food_id}/log',logged_on=DAY,meal='dinner',grams=100)
                blocked=False; deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    with self.connect_db() as observer:
                        blocked=observer.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid<>pg_backend_pid() "
                            "AND wait_event_type='Lock' AND query LIKE %s)",
                            ('SELECT * FROM nutrition_food WHERE food_id=% FOR SHARE',)).fetchone()[0]
                    if blocked or future.done():
                        break
                    time.sleep(0.02)
            self.assertEqual(future.result(timeout=10).status_code,302)
        self.assertTrue(blocked,'Snapshot must serialize with a food edit')
        self.assertEqual(self.entries()[0][5],'Changed together')
        self.assertEqual(self.entries()[0][7],D('500'))
