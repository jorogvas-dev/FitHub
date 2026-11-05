"""Profile/bodyweight flows against PostgreSQL in an isolated test schema."""
from datetime import date, timedelta
from decimal import Decimal

from test_plans_postgres import PostgresTestCase


class ProfileIntegrationTests(PostgresTestCase):
    def user_id(self, client):
        with client.session_transaction() as state:
            return state['user_id']

    def profile(self, client=None):
        with self.connect_db() as conn:
            return conn.execute('SELECT height_cm,goal,experience FROM fitness_profile WHERE user_id=%s',
                                (self.user_id(client or self.a),)).fetchone()

    def measurements(self, client=None):
        with self.connect_db() as conn:
            return conn.execute('SELECT measurement_id,measured_on,weight_kg FROM body_measurement '
                                'WHERE user_id=%s ORDER BY measured_on DESC',
                                (self.user_id(client or self.a),)).fetchall()

    def add_measurement(self, measured_on='2024-02-29', weight='80.25', client=None, **extra):
        return self.post(client or self.a, '/bodyweight/new', measured_on=measured_on, weight_kg=weight, **extra)

    def test_profile_edit_clear_and_database_bounds(self):
        self.assertEqual(self.a.get('/profile').status_code, 200)
        self.assertEqual(self.profile(), (None, None, None))
        for height, goal, experience in [('50', 'strength', 'beginner'),
                                          ('300', 'muscle_gain', 'intermediate'),
                                          ('175.5', 'fat_loss', 'advanced'),
                                          (' 180.1 ', 'general_fitness', 'beginner')]:
            with self.subTest(goal=goal):
                response = self.post(self.a, '/profile', height_cm=height, goal=goal, experience=experience)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.profile(), (Decimal(height), goal, experience))
                self.assertIn(str(Decimal(height)).encode(), self.a.get('/profile').data)
        self.assertEqual(self.post(self.a, '/profile', height_cm=' ', goal='', experience='').status_code, 302)
        self.assertEqual(self.profile(), (None, None, None))
        self.assertNotIn(b'value="None"', self.a.get('/profile').data)

    def test_profile_validation_is_atomic_and_preserves_submitted_form(self):
        valid = dict(height_cm='175.5', goal='strength', experience='intermediate')
        self.post(self.a, '/profile', **valid)
        original = self.profile()
        invalid = [dict(height_cm=value) for value in ['49.9', '300.1', '-1', '0', '175.55',
                   'NaN', 'sNaN', 'Infinity', '-Infinity', '1e2', 'abc', '9'*100, "175; DROP TABLE app_user"]]
        invalid += [dict(goal='unknown'), dict(experience='expert'), dict(goal="strength'; DROP TABLE exercise; --")]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                response = self.post(self.a, '/profile', **{**valid, **overrides})
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'role="alert"', response.data)
                self.assertEqual(self.profile(), original)
        response = self.post(self.a, '/profile', height_cm='175.55', goal='fat_loss', experience='advanced')
        self.assertIn(b'value="175.55"', response.data)
        self.assertIn(b'value="fat_loss" selected', response.data)
        self.assertEqual(self.profile(), original)

    def test_profile_ownership_and_accounts_without_profile_rows(self):
        self.post(self.a, '/profile', height_cm='199.9', goal='strength', experience='advanced')
        original = self.profile()
        self.assertNotIn(b'199.9', self.b.get(f'/profile?user_id={self.owner}').data)
        self.assertEqual(self.post(self.b, f'/profile?user_id={self.owner}', user_id=self.owner,
                                  height_cm='155.5', goal='fat_loss', experience='beginner').status_code, 302)
        self.assertEqual(self.profile(), original)
        self.assertEqual(self.profile(self.b), (Decimal('155.5'), 'fat_loss', 'beginner'))
        with self.connect_db() as conn:
            conn.execute('DELETE FROM fitness_profile WHERE user_id=%s', (self.owner,))
        self.assertEqual(self.a.get('/profile').status_code, 200)
        self.assertEqual(self.post(self.a, '/profile', height_cm=180, goal='strength').status_code, 302)
        self.assertEqual(self.profile(), (Decimal('180.0'), 'strength', None))

    def test_bodyweight_crud_history_and_delete_confirmation(self):
        self.assertIn(b'No measurements yet', self.a.get('/bodyweight').data)
        self.assertIn(f'max="{date.today()}"'.encode(), self.a.get('/bodyweight/new').data)
        self.assertEqual(self.add_measurement(date.today().isoformat(), ' 80.25 ').status_code, 302)
        entry = self.measurements()[0]
        self.assertEqual(entry[1:], (date.today(), Decimal('80.25')))
        edit = f'/bodyweight/{entry[0]}/edit'; delete = f'/bodyweight/{entry[0]}/delete'
        self.assertIn(b'value="80.25"', self.a.get(edit).data)
        self.assertEqual(self.post(self.a, edit, measured_on='2024-02-29', weight_kg='79.10').status_code, 302)
        self.assertEqual(self.measurements(), [(entry[0], date(2024, 2, 29), Decimal('79.10'))])
        history = self.a.get('/bodyweight')
        self.assertIn(b'2024-02-29', history.data); self.assertIn(b'79.10 kg', history.data)
        self.assertIn(edit.encode(), history.data); self.assertIn(delete.encode(), history.data)
        self.assertEqual(history.headers['Cache-Control'], 'no-store')
        self.assertIn(b'name="confirm"', self.a.get(delete).data)
        self.assertEqual(len(self.measurements()), 1)  # GET only displays confirmation.
        for confirm in ['', 'yes', 'false']:
            self.assertEqual(self.post(self.a, delete, confirm=confirm).status_code, 400)
            self.assertEqual(len(self.measurements()), 1)
        self.assertEqual(self.post(self.a, delete, confirm='delete').status_code, 302)
        self.assertEqual(self.measurements(), [])
        self.assertIn(b'No measurements yet', self.a.get('/bodyweight').data)

    def test_measurement_ownership_including_guessed_ids_and_forged_user_fields(self):
        self.add_measurement(weight='81.23')
        original = self.measurements(); entry_id = original[0][0]
        for suffix in ['edit', 'delete']:
            path = f'/bodyweight/{entry_id}/{suffix}'
            self.assertEqual(self.b.get(path).status_code, 404)
            self.assertEqual(self.post(self.b, path, confirm='delete', measured_on='2024-01-01',
                                      weight_kg=100, user_id=self.owner).status_code, 404)
        self.assertNotIn(b'81.23', self.b.get(f'/bodyweight?user_id={self.owner}').data)
        self.assertEqual(self.add_measurement(weight='65.67', client=self.b, user_id=self.owner).status_code, 302)
        self.assertEqual(self.measurements(), original)
        bob_id = self.measurements(self.b)[0][0]
        self.assertEqual(self.post(self.b, f'/bodyweight/{bob_id}/edit', user_id=self.owner,
                                  measured_on='2024-01-01', weight_kg=66).status_code, 302)
        self.assertEqual(self.measurements(), original)
        self.assertEqual(self.measurements(self.b)[0][1:], (date(2024, 1, 1), Decimal('66.00')))
        self.assertNotIn(b'66.00', self.a.get('/bodyweight').data)
        for suffix in ['edit', 'delete']:
            self.assertEqual(self.a.get(f'/bodyweight/999999/{suffix}').status_code, 404)
            self.assertEqual(self.post(self.a, f'/bodyweight/999999/{suffix}').status_code, 404)

    def test_invalid_measurements_never_insert_or_partially_edit(self):
        self.add_measurement()
        original = self.measurements(); path = f'/bodyweight/{original[0][0]}/edit'
        invalid = [dict(weight_kg=value) for value in ['', ' ', '19.99', '500.01', '-20', '0',
                   '80.001', 'NaN', 'sNaN', 'Infinity', '-Infinity', '1e2', '80,25', '9'*100,
                   "80'; DROP TABLE body_measurement; --"]]
        invalid += [dict(measured_on=value) for value in ['', ' ', '2023-02-29', '2024-02-30',
                    '2024-13-01', '2024-01-32', '0000-01-01', '20240229', '2024-W09-4',
                    '29/02/2024', '2024-02-29T00:00:00', (date.today()+timedelta(days=1)).isoformat()]]
        valid = dict(measured_on='2024-01-01', weight_kg='81.25')
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                data = {**valid, **overrides}
                for target in ['/bodyweight/new', path]:
                    response = self.post(self.a, target, **data)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b'role="alert"', response.data)
                    self.assertEqual(self.measurements(), original)
        response = self.add_measurement((date.today()+timedelta(days=1)).isoformat())
        self.assertIn(b'cannot be in the future', response.data)

    def test_measurement_bounds_and_duplicate_dates_preserve_existing_values(self):
        self.assertEqual(self.add_measurement('2024-01-01', '20').status_code, 302)
        self.assertEqual(self.add_measurement('2024-02-01', '500').status_code, 302)
        original = self.measurements()
        duplicate = self.add_measurement('2024-01-01', '80.25')
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn(b'already have a measurement for that date', duplicate.data)
        self.assertIn(b'value="80.25"', duplicate.data)
        self.assertEqual(self.measurements(), original)
        # Renaming the date of an existing entry must not overwrite either entry.
        path = f'/bodyweight/{original[0][0]}/edit'
        response = self.post(self.a, path, measured_on='2024-01-01', weight_kg='90.25')
        self.assertIn(b'already have a measurement for that date', response.data)
        self.assertEqual(self.measurements(), original)
        self.assertEqual(self.post(self.a, path, measured_on='2024-02-01', weight_kg='499.99').status_code, 302)
        self.assertEqual(self.measurements()[0][2], Decimal('499.99'))
        # Dates are unique per user, so another user can record the same date.
        self.assertEqual(self.add_measurement('2024-01-01', '80', client=self.b).status_code, 302)

    def test_authentication_csrf_and_methods(self):
        self.add_measurement(); entry_id = self.measurements()[0][0]
        mutations = ['/profile', '/bodyweight/new', f'/bodyweight/{entry_id}/edit', f'/bodyweight/{entry_id}/delete']
        anonymous = self.app.test_client()
        for path in ['/bodyweight', *mutations]:
            self.assertTrue(anonymous.get(path).location.endswith('/login'), path)
        for path in mutations:
            self.assertTrue(self.post(anonymous, path).location.endswith('/login'), path)
            for token in [None, 'wrong']:
                self.assertEqual(self.a.post(path, data={} if token is None else {'csrf_token':token}).status_code, 400)
            self.assertEqual(self.a.put(path).status_code, 405)
            self.assertEqual(self.a.delete(path).status_code, 405)
        self.assertEqual(len(self.measurements()), 1)
        for link in [b'/profile', b'/bodyweight']:
            self.assertIn(link, self.a.get('/dashboard').data)

    def test_dashboard_no_measurements_or_one_measurement(self):
        self.add_measurement(weight='499.99', client=self.b)
        page = self.a.get('/dashboard').data
        self.assertIn(b'No measurements recorded.', page)
        self.assertNotIn(b'499.99', page)
        self.assertNotIn(b'kg since', page)
        self.add_measurement(weight='80.25')
        page = self.a.get('/dashboard').data
        self.assertIn(b'80.25 kg', page)
        self.assertIn(b'2024-02-29', page)
        self.assertIn(b'Add another measurement to see your change.', page)
        self.assertNotIn(b'kg since', page)

    def test_dashboard_decimal_changes_use_measurement_dates_and_recalculate_after_crud(self):
        self.add_measurement('2024-05-01', '80.10')
        self.add_measurement('2024-06-01', '79.90')
        self.add_measurement('2024-01-01', '100')  # Inserted last, but chronologically oldest.
        self.add_measurement(date.today().isoformat(), '499.99', client=self.b)
        newest, previous, oldest = self.measurements()
        page = self.a.get('/dashboard').data
        self.assertIn(b'79.90 kg', page)
        self.assertIn(b'-0.20 kg since 2024-05-01', page)
        self.assertNotIn(b'499.99', page)
        history = self.a.get('/bodyweight').data
        self.assertLess(history.index(b'2024-06-01'), history.index(b'2024-05-01'))
        for weight, expected in [('80.20', b'+0.10'), ('80.10', b'0.00')]:
            self.post(self.a, f'/bodyweight/{newest[0]}/edit', measured_on='2024-06-01', weight_kg=weight)
            self.assertIn(expected+b' kg since 2024-05-01', self.a.get('/dashboard').data)
        self.post(self.a, f'/bodyweight/{newest[0]}/delete', confirm='delete')
        self.assertIn(b'-19.90 kg since 2024-01-01', self.a.get('/dashboard').data)
        # Moving an old entry to the newest date changes both the latest and comparison.
        self.post(self.a, f'/bodyweight/{oldest[0]}/edit', measured_on='2024-07-01', weight_kg='85.10')
        self.assertIn(b'+5.00 kg since 2024-05-01', self.a.get('/dashboard').data)
        self.post(self.a, f'/bodyweight/{oldest[0]}/delete', confirm='delete')
        self.assertIn(b'Add another measurement', self.a.get('/dashboard').data)
        self.post(self.a, f'/bodyweight/{previous[0]}/delete', confirm='delete')
        self.assertIn(b'No measurements recorded.', self.a.get('/dashboard').data)

    def test_profile_and_bodyweight_changes_preserve_accounts_plans_and_logs(self):
        plan = self.create_plan(); day = self.create_day(plan); self.create_entry(plan, day)
        response = self.post(self.a, f'/workouts/start/{plan}/{day}')
        workout = int(response.location.rsplit('/', 1)[1])
        self.post(self.a, f'/workouts/{workout}/sets/new', exercise_id=self.exercise, set_number=1, reps=8)
        self.post(self.a, f'/workouts/{workout}/finish')
        tables = ['app_user', 'workout_plan', 'workout_day', 'plan_exercise', 'workout_session', 'set_log']
        with self.connect_db() as conn:
            # Static table names from this test; no request input is interpolated.
            before = {table: conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() for table in tables}
        self.post(self.a, '/profile', height_cm='180.1', goal='strength', experience='advanced')
        self.add_measurement()
        entry_id = self.measurements()[0][0]
        self.post(self.a, f'/bodyweight/{entry_id}/edit', measured_on='2024-01-01', weight_kg=82)
        self.post(self.a, f'/bodyweight/{entry_id}/delete', confirm='delete')
        with self.connect_db() as conn:
            after = {table: conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() for table in tables}
        self.assertEqual(before, after)
        self.assertEqual(self.a.get(f'/workouts/{workout}').status_code, 200)
