"""Real PostgreSQL tests. Set FITHUB_TEST_DATABASE_URL to a disposable test DB.
Each run uses a unique schema and removes only that schema afterwards.
"""
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch
import psycopg
from psycopg import sql
from app import create_app

URI = os.environ.get('FITHUB_TEST_DATABASE_URL')

@unittest.skipUnless(URI, 'Set FITHUB_TEST_DATABASE_URL for PostgreSQL integration checks')
class PostgresTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema='fit_test_'+uuid.uuid4().hex
        with psycopg.connect(URI) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
        with cls.connect_db() as conn:
            conn.execute((Path(__file__).resolve().parents[1]/'db/init/01-schema.sql').read_text())
            conn.execute((Path(__file__).resolve().parents[1]/'db/init/02-exercise-library.sql').read_text())
            conn.execute((Path(__file__).resolve().parents[1]/'db/init/03-nutrition.sql').read_text())
            conn.execute((Path(__file__).resolve().parents[1]/'db/init/04-food-catalogue.sql').read_text())
            conn.execute((Path(__file__).resolve().parents[1]/'db/init/05-meal-planner.sql').read_text())
            conn.execute((Path(__file__).resolve().parents[1]/'db/init/06-usda-source-links.sql').read_text())

    @classmethod
    def connect_db(cls):
        return psycopg.connect(URI,options='-c search_path='+cls.schema)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(URI) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(cls.schema)))

    def setUp(self):
        for module in ['auth','main','plans','workouts','profile','admin','nutrition','food_library','meals']:
            p=patch('app.routes.'+module+'.connect',self.connect_db)
            p.start();self.addCleanup(p.stop)
        p=patch('app.cli.connect',self.connect_db)
        p.start();self.addCleanup(p.stop)
        self.app=create_app({'TESTING':True,'SECRET_KEY':'integration-test-only'})
        self.a=self.app.test_client();self.b=self.app.test_client()
        for client,name in [(self.a,'alice'),(self.b,'bob')]:
            self.assertEqual(self.post(client,'/register',username=name,email=name+'@example.com',
                password='test-password-123',confirm_password='test-password-123').status_code,302)
        with self.a.session_transaction() as state: self.owner=state['user_id']
        with self.connect_db() as conn:
            self.exercise=conn.execute('SELECT min(exercise_id) FROM exercise').fetchone()[0]

    def tearDown(self):
        with self.connect_db() as conn: conn.execute('TRUNCATE app_user CASCADE')

    def post(self,client,path,**data):
        with client.session_transaction() as state:
            if 'csrf_token' not in state: state['csrf_token']='test-token'
            token=state['csrf_token']
        return client.post(path,data={'csrf_token':token,**data})

    def create_plan(self,name='Strength block',client=None):
        r=self.post(client or self.a,'/plans/new',name=name)
        self.assertEqual(r.status_code,302)
        return int(r.location.rsplit('/',1)[1])

    def create_day(self,plan,name='Upper A'):
        self.assertEqual(self.post(self.a,f'/plans/{plan}/days/new',name=name).status_code,302)
        with self.connect_db() as conn:
            return conn.execute('SELECT day_id FROM workout_day WHERE plan_id=%s ORDER BY day_id DESC LIMIT 1',(plan,)).fetchone()[0]

    def create_entry(self,plan,day):
        path=f'/plans/{plan}/days/{day}/exercises/new'
        self.assertEqual(self.post(self.a,path,exercise_id=self.exercise,target_sets=4,reps_min=6,reps_max=10).status_code,302)
        with self.connect_db() as conn:
            return conn.execute('SELECT plan_exercise_id FROM plan_exercise WHERE day_id=%s ORDER BY plan_exercise_id DESC LIMIT 1',(day,)).fetchone()[0]

class PlanIntegrationTests(PostgresTestCase):
    def test_full_create_edit_flow_and_templates(self):
        plan=self.create_plan();day=self.create_day(plan);entry=self.create_entry(plan,day)
        paths=['/plans',f'/plans/{plan}',f'/plans/{plan}/edit',f'/plans/{plan}/delete',
               f'/plans/{plan}/days/new',f'/plans/{plan}/days/{day}/edit',f'/plans/{plan}/days/{day}/delete',
               f'/plans/{plan}/days/{day}/exercises/new',
               f'/plans/{plan}/days/{day}/exercises/{entry}/edit',f'/plans/{plan}/days/{day}/exercises/{entry}/delete']
        for path in paths:
            self.assertEqual(self.a.get(path).status_code,200,path)
        self.assertEqual(self.post(self.a,f'/plans/{plan}/edit',name='New block').status_code,302)
        self.assertEqual(self.post(self.a,f'/plans/{plan}/days/{day}/edit',name='Upper B').status_code,302)
        self.assertEqual(self.post(self.a,f'/plans/{plan}/days/{day}/exercises/{entry}/edit',exercise_id=self.exercise,target_sets=5,reps_min=8,reps_max=12).status_code,302)
        page=self.a.get(f'/plans/{plan}').data
        self.assertIn(b'New block',page);self.assertIn(b'Upper B',page)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT target_sets,reps_min,reps_max FROM plan_exercise WHERE plan_exercise_id=%s',(entry,)).fetchone(),(5,8,12))
        self.assertEqual(self.a.get('/dashboard').status_code,200)

    def test_second_member_cannot_read_or_mutate_any_route(self):
        plan=self.create_plan();day=self.create_day(plan);entry=self.create_entry(plan,day)
        base=f'/plans/{plan}'
        paths=[base,base+'/edit',base+'/delete',base+'/days/new',
               f'{base}/days/{day}/edit',f'{base}/days/{day}/delete',
               f'{base}/days/{day}/exercises/new',f'{base}/days/{day}/exercises/{entry}/edit',
               f'{base}/days/{day}/exercises/{entry}/delete']
        for path in paths:
            self.assertEqual(self.b.get(path).status_code,404,path)
            if path!=base:
                self.assertEqual(self.post(self.b,path,name='Stolen',confirm='delete',exercise_id=self.exercise,target_sets=3,reps_min=6,reps_max=10).status_code,404,path)
        self.assertNotIn(b'Strength block',self.b.get('/plans').data)
        self.assertIn(b'Strength block',self.a.get('/plans').data)

    def test_mismatched_parent_child_urls_are_rejected(self):
        plan=self.create_plan();day=self.create_day(plan);entry=self.create_entry(plan,day)
        other=self.create_plan('Second block');other_day=self.create_day(other)
        self.assertEqual(self.post(self.a,f'/plans/{other}/days/{day}/edit',name='Wrong').status_code,404)
        self.assertEqual(self.post(self.a,f'/plans/{other}/days/{other_day}/exercises/{entry}/delete',confirm='delete').status_code,404)
        self.assertEqual(self.a.get(f'/plans/{plan}/days/{day}/exercises/999999/edit').status_code,404)

    def test_invalid_inputs_do_not_save_partial_changes(self):
        self.assertEqual(self.post(self.a,'/plans/new',name=' ').status_code,200)
        plan=self.create_plan();day=self.create_day(plan);entry=self.create_entry(plan,day)
        path=f'/plans/{plan}/days/{day}/exercises/{entry}/edit'
        valid=dict(exercise_id=self.exercise,target_sets=4,reps_min=6,reps_max=10)
        for overrides in [dict(target_sets=0),dict(target_sets=31),dict(target_sets='1.5'),dict(reps_min=11),dict(reps_max=1001),dict(exercise_id=999999),dict(reps_min='1e2')]:
            r=self.post(self.a,path,**{**valid,**overrides})
            self.assertEqual(r.status_code,200)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT target_sets,reps_min,reps_max FROM plan_exercise WHERE plan_exercise_id=%s',(entry,)).fetchone(),(4,6,10))
        self.assertEqual(self.post(self.a,f'/plans/{plan}/delete').status_code,400)
        self.assertEqual(self.a.get(f'/plans/{plan}').status_code,200)
        self.assertEqual(self.a.post(f'/plans/{plan}/edit',data={'name':'CSRF'}).status_code,400)

    def test_names_are_parameterised_and_escaped(self):
        name="<script>alert('x')</script>'; DROP TABLE exercise; --"
        plan=self.create_plan(name)
        page=self.a.get(f'/plans/{plan}').data
        self.assertIn(b'&lt;script&gt;',page);self.assertNotIn(b'<script>',page)
        with self.connect_db() as conn:
            self.assertGreaterEqual(conn.execute('SELECT count(*) FROM exercise').fetchone()[0],50)

    def test_delete_plan_preserves_workout_history(self):
        plan=self.create_plan();day=self.create_day(plan);self.create_entry(plan,day)
        with self.connect_db() as conn:
            session=conn.execute('INSERT INTO workout_session(user_id,day_id) VALUES (%s,%s) RETURNING session_id',(self.owner,day)).fetchone()[0]
            conn.execute('INSERT INTO set_log(session_id,exercise_id,set_number,reps) VALUES (%s,%s,1,8)',(session,self.exercise))
        self.assertEqual(self.post(self.a,f'/plans/{plan}/delete',confirm='delete').status_code,302)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT day_id FROM workout_session WHERE session_id=%s',(session,)).fetchone(),(None,))
            self.assertEqual(conn.execute('SELECT count(*) FROM set_log WHERE session_id=%s',(session,)).fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT count(*) FROM workout_day WHERE plan_id=%s',(plan,)).fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT count(*) FROM plan_exercise').fetchone()[0],0)

    def test_remove_and_append_days_and_exercises(self):
        plan=self.create_plan();day1=self.create_day(plan,'First');day2=self.create_day(plan,'Second')
        e1=self.create_entry(plan,day2);self.create_entry(plan,day2)
        self.assertEqual(self.post(self.a,f'/plans/{plan}/days/{day2}/exercises/{e1}/delete',confirm='delete').status_code,302)
        self.create_entry(plan,day2)
        self.assertEqual(self.post(self.a,f'/plans/{plan}/days/{day1}/delete',confirm='delete').status_code,302)
        self.create_day(plan,'Third')
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT position FROM workout_day WHERE plan_id=%s ORDER BY position',(plan,)).fetchall(),[(2,),(3,)])
            self.assertEqual(conn.execute('SELECT position FROM plan_exercise WHERE day_id=%s ORDER BY position',(day2,)).fetchall(),[(2,),(3,)])

if __name__=='__main__': unittest.main()
