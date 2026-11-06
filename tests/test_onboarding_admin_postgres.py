"""Onboarding, administrator boundaries and retained exercise history on PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.routes.profile import query as profile_query
from test_plans_postgres import PostgresTestCase


class OnboardingTests(PostgresTestCase):
    def values(self):
        with self.connect_db() as conn:
            profile = conn.execute('SELECT height_cm,goal,experience FROM fitness_profile WHERE user_id=%s', (self.owner,)).fetchone()
            weights = conn.execute('SELECT measured_on,weight_kg FROM body_measurement WHERE user_id=%s ORDER BY measured_on', (self.owner,)).fetchall()
        return profile, weights

    def onboard(self, client=None, **overrides):
        data = dict(height_cm='175.5', goal='strength', experience='beginner', weight_kg='80.25')
        return self.post(client or self.a, '/onboarding', **{**data, **overrides})

    def test_registration_redirect_and_complete_setup(self):
        new = self.app.test_client()
        response = self.post(new, '/register', username='newmember', email='new@example.com',
                             password='test-password-123', confirm_password='test-password-123', is_admin='true')
        self.assertTrue(response.location.endswith('/onboarding'))
        self.assertIn(b'Finish setup', new.get('/dashboard').data)
        self.assertEqual(self.onboard(new).status_code, 302)
        self.assertNotIn(b'Finish setup', new.get('/dashboard').data)
        with new.session_transaction() as state:
            user_id = state['user_id']
        with self.connect_db() as conn:
            self.assertFalse(conn.execute('SELECT is_admin FROM app_user WHERE user_id=%s', (user_id,)).fetchone()[0])
            self.assertEqual(conn.execute('SELECT height_cm,goal,experience FROM fitness_profile WHERE user_id=%s', (user_id,)).fetchone(),
                             (Decimal('175.5'), 'strength', 'beginner'))
            self.assertEqual(conn.execute('SELECT measured_on,weight_kg FROM body_measurement WHERE user_id=%s', (user_id,)).fetchone(),
                             (date.today(), Decimal('80.25')))

    def test_validation_is_reused_and_all_writes_are_atomic(self):
        invalid = [dict(height_cm='NaN'), dict(height_cm='49.9'), dict(height_cm='300.1'),
                   dict(height_cm='175.55'), dict(goal=''), dict(goal='invalid'), dict(experience='expert'),
                   dict(weight_kg='Infinity'), dict(weight_kg='19.99'), dict(weight_kg='500.01'),
                   dict(weight_kg='80.001'), dict(weight_kg='')]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                response = self.onboard(**overrides)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'role="alert"', response.data)
                self.assertEqual(self.values(), ((None, None, None), []))
        self.assertEqual(self.onboard(height_cm=50, weight_kg=500).status_code, 302)
        original = self.values()
        self.assertEqual(self.onboard(height_cm=300, weight_kg=20).status_code, 302)
        self.assertEqual(self.values(), original)  # Replaying setup never replaces completed values.

    def test_existing_values_and_todays_measurement_need_explicit_confirmation(self):
        with self.connect_db() as conn:
            conn.execute("UPDATE fitness_profile SET height_cm=180.1,goal='fat_loss' WHERE user_id=%s", (self.owner,))
            conn.execute('INSERT INTO body_measurement(user_id,measured_on,weight_kg) VALUES (%s,%s,77.75)', (self.owner,date.today()))
        page = self.a.get('/onboarding').data
        self.assertIn(b'77.75', page)
        self.assertIn(b'keep_today', page)
        self.assertIn(b'readonly', page)
        self.assertIn(b'Confirm', self.onboard().data)
        self.assertEqual(self.values()[0], (Decimal('180.1'), 'fat_loss', None))
        response = self.onboard(height_cm='NaN', goal='invalid', weight_kg=999,
                                weight_action='keep_today', experience='advanced', user_id=999)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.values(), ((Decimal('180.1'), 'fat_loss', 'advanced'), [(date.today(), Decimal('77.75'))]))

    def test_previous_measurement_can_be_kept_or_a_new_today_entry_added(self):
        self.post(self.a, '/bodyweight/new', measured_on='2024-01-01', weight_kg='75.5')
        self.assertEqual(self.onboard(weight_action='keep_latest', weight_kg='NaN').status_code, 302)
        self.assertEqual(self.values()[1], [(date(2024,1,1),Decimal('75.50'))])
        self.post(self.b, '/bodyweight/new', measured_on='2024-01-01', weight_kg='65.5')
        self.assertEqual(self.onboard(self.b, weight_action='add_today', weight_kg='66.5').status_code, 302)
        with self.b.session_transaction() as state:
            other = state['user_id']
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT measured_on,weight_kg FROM body_measurement WHERE user_id=%s ORDER BY measured_on', (other,)).fetchall(),
                             [(date(2024,1,1),Decimal('65.50')),(date.today(),Decimal('66.50'))])

    def test_duplicate_today_rolls_back_profile_fill_and_reloads_existing_entry(self):
        self.post(self.a, '/bodyweight/new', measured_on=date.today().isoformat(), weight_kg=78)
        hidden = False
        def stale_read(conn, statement, params=(), one=False):
            nonlocal hidden
            if 'FROM body_measurement' in statement and 'AND measured_on=' in statement and not hidden:
                hidden = True
                return None  # Model another form inserting today after the initial read.
            return profile_query(conn, statement, params, one)
        with patch('app.routes.profile.query', side_effect=stale_read):
            response = self.onboard(weight_action='add_today')
        self.assertIn(b'just recorded', response.data)
        self.assertIn(b'78.00', response.data)
        self.assertEqual(self.values(), ((None,None,None), [(date.today(),Decimal('78.00'))]))

    def test_concurrent_setup_and_ownership(self):
        clients = [self.app.test_client(), self.app.test_client()]
        for client in clients:
            with client.session_transaction() as state:
                state['user_id'] = self.owner
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda pair:self.onboard(pair[0], height_cm=pair[1]), zip(clients,[170,190])))
        self.assertEqual([r.status_code for r in responses], [302,302])
        self.assertIn(self.values()[0][0], (Decimal('170.0'),Decimal('190.0')))
        self.assertEqual(len(self.values()[1]),1)
        original = self.values()
        self.assertEqual(self.onboard(self.b, user_id=self.owner).status_code,302)
        self.assertEqual(self.values(), original)

    def test_protection_and_prompt_for_existing_incomplete_profile(self):
        self.assertIn(b'Finish setup',self.a.get('/dashboard').data)
        self.assertTrue(self.app.test_client().get('/onboarding').location.endswith('/login'))
        self.assertEqual(self.a.post('/onboarding').status_code,400)
        self.assertEqual(self.a.get('/onboarding').status_code,200)
        with self.connect_db() as conn:
            conn.execute('DELETE FROM fitness_profile WHERE user_id=%s',(self.owner,))
        self.assertEqual(self.onboard().status_code,302)
        self.assertNotIn(b'Finish setup',self.a.get('/dashboard').data)


class AdminExerciseTests(PostgresTestCase):
    def promote(self):
        result = self.app.test_cli_runner().invoke(args=['promote-admin','alice'])
        self.assertEqual(result.exit_code,0,result.output)

    def create_exercise(self, name='Custom training row', tracking_type='reps'):
        response = self.post(self.a,'/admin/exercises/new',name=name,muscle_group='Back',equipment='Cable',
                             description='Pull the handle with control.',tracking_type=tracking_type)
        self.assertEqual(response.status_code,302)
        with self.connect_db() as conn:
            return conn.execute('SELECT exercise_id FROM exercise WHERE name=%s',(name,)).fetchone()[0]

    def test_every_admin_route_is_server_protected_even_with_forged_flags(self):
        exercise = self.exercise
        paths=['/admin/exercises','/admin/exercises/new',f'/admin/exercises/{exercise}/edit',
               f'/admin/exercises/{exercise}/deactivate',f'/admin/exercises/{exercise}/activate']
        with self.a.session_transaction() as state:
            state['is_admin']=True
        for path in paths:
            self.assertEqual(self.post(self.a,path,is_admin='true').status_code,403 if path!='/admin/exercises' else 405,path)
        for path in paths[:-1]:
            self.assertEqual(self.a.get(path).status_code,403,path)
            self.assertTrue(self.app.test_client().get(path).location.endswith('/login'),path)
        self.assertNotIn(b'href="/admin/exercises"',self.a.get('/dashboard').data)
        self.promote()
        self.assertIn(b'href="/admin/exercises"',self.a.get('/dashboard').data)
        with self.connect_db() as conn:
            conn.execute('UPDATE app_user SET is_admin=false WHERE user_id=%s',(self.owner,))
        self.assertEqual(self.a.get('/admin/exercises').status_code,403)

    def test_admin_crud_validation_duplicates_csrf_and_confirmation(self):
        self.promote()
        for path in ['/admin/exercises','/admin/exercises/new']:
            self.assertEqual(self.a.get(path).status_code,200)
        exercise=self.create_exercise()
        edit=f'/admin/exercises/{exercise}/edit'; deactivate=f'/admin/exercises/{exercise}/deactivate'
        valid=dict(name='Custom training row',muscle_group='Back',equipment='Cable',description='Pull with control.',tracking_type='reps')
        for override in [dict(name=''),dict(name='x'*101),dict(muscle_group='x'*41),dict(equipment='x'*41),dict(description=''),dict(description='x'*2001),dict(tracking_type='seconds')]:
            self.assertEqual(self.post(self.a,edit,**{**valid,**override}).status_code,200)
        self.assertEqual(self.post(self.a,edit,**{**valid,'name':'Pull-up'}).status_code,200)
        self.assertIn(b'already in the library',self.post(self.a,'/admin/exercises/new',**valid).data)
        self.assertEqual(self.a.post(edit).status_code,400)
        self.assertEqual(self.a.post(deactivate).status_code,400)
        self.assertEqual(self.a.get(deactivate).status_code,200)
        self.assertEqual(self.post(self.a,deactivate).status_code,400)
        self.assertEqual(self.post(self.a,deactivate,confirm='deactivate').status_code,302)
        self.assertNotIn(b'Custom training row',self.a.get('/exercises?q=Custom').data)
        self.assertIn(b'Inactive',self.a.get('/admin/exercises?q=Custom').data)
        self.assertEqual(self.post(self.a,f'/admin/exercises/{exercise}/activate').status_code,302)
        self.assertIn(b'Custom training row',self.a.get('/exercises?q=Custom').data)
        self.assertEqual(self.a.get('/admin/exercises/999999/edit').status_code,404)
        self.assertEqual(self.post(self.a,'/admin/exercises/999999/deactivate',confirm='deactivate').status_code,404)

    def test_deactivation_retains_prescriptions_logs_and_immutable_snapshots(self):
        self.promote(); exercise=self.create_exercise('Snapshot movement')
        plan=self.create_plan(); day=self.create_day(plan)
        self.post(self.a,f'/plans/{plan}/days/{day}/exercises/new',exercise_id=exercise,target_sets=3,reps_min=8,reps_max=12)
        response=self.post(self.a,f'/workouts/start/{plan}/{day}')
        workout=int(response.location.rsplit('/',1)[1])
        self.post(self.a,f'/workouts/{workout}/sets/new',exercise_id=exercise,set_number=1,reps=10)
        with self.connect_db() as conn:
            original=conn.execute('SELECT notes FROM workout_session WHERE session_id=%s',(workout,)).fetchone()[0]
            entry=conn.execute('SELECT plan_exercise_id FROM plan_exercise WHERE day_id=%s',(day,)).fetchone()[0]
        self.post(self.a,f'/admin/exercises/{exercise}/edit',name='Renamed snapshot movement',muscle_group='Back',equipment='Cable',description='New instructions.',tracking_type='reps')
        self.post(self.a,f'/admin/exercises/{exercise}/deactivate',confirm='deactivate')
        self.assertIn(b'Inactive',self.a.get(f'/plans/{plan}').data)
        self.assertIn(b'Snapshot movement',self.a.get(f'/workouts/{workout}').data)
        response=self.post(self.a,f'/plans/{plan}/days/{day}/exercises/new',exercise_id=exercise,target_sets=3,reps_min=8,reps_max=12)
        self.assertIn(b'deactivated',response.data)
        self.assertNotIn(b'Renamed snapshot movement',self.a.get(f'/plans/{plan}/days/{day}/exercises/new').data)
        self.assertEqual(self.post(self.a,f'/plans/{plan}/days/{day}/exercises/{entry}/edit',exercise_id=exercise,target_sets=4,reps_min=8,reps_max=12).status_code,302)
        # Existing workouts can still log their assigned deactivated exercise.
        self.assertEqual(self.post(self.a,f'/workouts/{workout}/sets/new',exercise_id=exercise,set_number=2,reps=8).status_code,302)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT notes FROM workout_session WHERE session_id=%s',(workout,)).fetchone()[0],original)
            self.assertEqual(conn.execute('SELECT count(*) FROM set_log WHERE session_id=%s',(workout,)).fetchone()[0],2)

    def test_timed_library_and_legacy_records_never_treat_seconds_as_reps(self):
        with self.connect_db() as conn:
            timed=conn.execute("SELECT exercise_id FROM exercise WHERE name='Plank'").fetchone()[0]
        self.assertIn(b'Timed',self.a.get('/exercises?q=Plank').data)
        plan=self.create_plan(); day=self.create_day(plan)
        path=f'/plans/{plan}/days/{day}/exercises/new'
        response=self.post(self.a,path,exercise_id=timed,target_sets=3,reps_min=30,reps_max=60)
        self.assertIn(b'Do not enter seconds as reps',response.data)
        self.assertNotIn(b'>Plank',self.a.get(path).data)
        with self.connect_db() as conn:
            conn.execute('INSERT INTO plan_exercise(day_id,exercise_id,position,target_sets,reps_min,reps_max) VALUES (%s,%s,1,3,30,60)',(day,timed))
        self.assertIn(b'Legacy values retained',self.a.get(f'/plans/{plan}').data)
        response=self.post(self.a,f'/workouts/start/{plan}/{day}')
        workout=int(response.location.rsplit('/',1)[1])
        with self.connect_db() as conn:
            conn.execute('INSERT INTO set_log(session_id,exercise_id,set_number,reps) VALUES (%s,%s,1,30)',(workout,timed))
        self.assertIn(b'legacy values retained',self.a.get(f'/workouts/{workout}').data)
        self.assertIn(b'Do not enter seconds as reps',self.post(self.a,f'/workouts/{workout}/sets/new',exercise_id=timed,set_number=2,reps=30).data)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT reps FROM set_log WHERE session_id=%s',(workout,)).fetchall(),[(30,)])

    def test_admin_names_are_parameterized_and_escaped_and_promotion_is_explicit(self):
        self.assertNotEqual(self.app.test_cli_runner().invoke(args=['promote-admin','missing']).exit_code,0)
        self.promote()
        name="<script>alert('x')</script>; DROP TABLE exercise; --"
        self.create_exercise(name)
        page=self.a.get('/admin/exercises?q=%3Cscript%3E').data
        self.assertIn(b'&lt;script&gt;',page); self.assertNotIn(b'<script>',page)
        with self.connect_db() as conn:
            self.assertFalse(conn.execute("SELECT is_admin FROM app_user WHERE username='bob'").fetchone()[0])
            self.assertGreaterEqual(conn.execute('SELECT count(*) FROM exercise').fetchone()[0],50)
