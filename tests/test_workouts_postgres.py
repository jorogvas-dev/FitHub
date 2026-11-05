"""Workout flows and real PostgreSQL transaction/ownership regression checks."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import time

from test_plans_postgres import PostgresTestCase


class WorkoutIntegrationTests(PostgresTestCase):
    def start_workout(self, plan=None, day=None):
        if plan is None:
            plan = self.create_plan()
            day = self.create_day(plan)
            self.create_entry(plan, day)
        response = self.post(self.a, f'/workouts/start/{plan}/{day}')
        self.assertEqual(response.status_code, 302)
        return int(response.location.rsplit('/', 1)[1])

    def log_set(self, workout, **overrides):
        values = dict(exercise_id=self.exercise, set_number=1, reps=8, weight_kg='', rir='')
        return self.post(self.a, f'/workouts/{workout}/sets/new', **{**values, **overrides})

    def sets(self, workout):
        with self.connect_db() as conn:
            return conn.execute('SELECT set_id,exercise_id,set_number,reps,weight_kg,rir FROM set_log '
                                'WHERE session_id=%s ORDER BY set_number', (workout,)).fetchall()

    def test_full_logging_flow_and_links(self):
        plan = self.create_plan(); day = self.create_day(plan); self.create_entry(plan, day)
        self.assertIn(b'Start workout', self.a.get(f'/plans/{plan}').data)
        workout = self.start_workout(plan, day)
        base = f'/workouts/{workout}'
        page = self.a.get(base)
        for text in [b'Upper A', b'Pull-up', b'Target sets', b'6', b'10', b'Log set', b'Finish workout']:
            self.assertIn(text, page.data)
        self.assertEqual(self.a.get(base+'/sets/new').status_code, 200)
        self.assertEqual(self.log_set(workout).status_code, 302)
        first = self.sets(workout)[0]
        self.assertEqual(first[1:], (self.exercise, 1, 8, None, None))
        self.assertIn(b'Bodyweight', self.a.get(base).data)
        edit = f'{base}/sets/{first[0]}/edit'
        self.assertEqual(self.a.get(edit).status_code, 200)
        self.assertEqual(self.post(self.a, edit, exercise_id=self.exercise, set_number=2,
                                   reps=9, weight_kg='12.50', rir=0).status_code, 302)
        self.assertEqual(self.sets(workout)[0][2:], (2, 9, Decimal('12.50'), 0))
        self.assertEqual(self.log_set(workout, set_number=3, reps=0, weight_kg=10000, rir=10).status_code, 302)
        self.assertEqual(self.post(self.a, f'{base}/sets/{first[0]}/delete').status_code, 302)
        self.assertEqual(len(self.sets(workout)), 1)
        self.assertIn(base.encode(), self.a.get('/workouts').data)
        self.assertIn(b'/workouts', self.a.get('/dashboard').data)
        self.assertEqual(self.post(self.a, base+'/finish').status_code, 302)
        page = self.a.get(base).data
        self.assertIn(b'read-only', page)
        self.assertNotIn(b'Finish workout', page)
        self.assertNotIn(b'Delete set', page)

    def test_other_user_cannot_access_or_mutate_sessions(self):
        workout = self.start_workout(); self.log_set(workout)
        set_id = self.sets(workout)[0][0]
        base = f'/workouts/{workout}'
        for path in [base, base+'/sets/new', f'{base}/sets/{set_id}/edit']:
            self.assertEqual(self.b.get(path).status_code, 404, path)
        for path in [base+'/sets/new', f'{base}/sets/{set_id}/edit',
                     f'{base}/sets/{set_id}/delete', base+'/finish']:
            self.assertEqual(self.post(self.b, path).status_code, 404, path)
        self.assertNotIn(b'Upper A', self.b.get('/workouts').data)
        self.assertEqual(len(self.sets(workout)), 1)

    def test_start_ownership_and_mismatched_parent_ids(self):
        plan = self.create_plan(); day = self.create_day(plan)
        other = self.create_plan('Other'); other_day = self.create_day(other)
        self.assertEqual(self.post(self.b, f'/workouts/start/{plan}/{day}').status_code, 404)
        self.assertEqual(self.post(self.a, f'/workouts/start/{plan}/{other_day}').status_code, 404)
        self.assertEqual(self.post(self.a, f'/workouts/start/{plan}/999999').status_code, 404)
        workout = self.start_workout(plan, day); other_workout = self.start_workout(other, other_day)
        self.log_set(workout); set_id = self.sets(workout)[0][0]
        path = f'/workouts/{other_workout}/sets/{set_id}'
        self.assertEqual(self.a.get(path+'/edit').status_code, 404)
        self.assertEqual(self.post(self.a, path+'/edit').status_code, 404)
        self.assertEqual(self.post(self.a, path+'/delete').status_code, 404)
        self.assertEqual(len(self.sets(workout)), 1)
        self.assertEqual(self.a.get('/workouts/999999').status_code, 404)

    def test_validation_and_duplicate_inserts_and_edits_are_atomic(self):
        workout = self.start_workout()
        self.log_set(workout, weight_kg='0', rir=0)
        original = self.sets(workout)
        invalid = [dict(exercise_id=999999), dict(exercise_id='1; DROP TABLE exercise'),
                   dict(set_number=0), dict(set_number=-1), dict(set_number=2147483648),
                   dict(set_number='1.5'), dict(set_number='1e2'), dict(set_number='9'*100),
                   dict(reps=-1), dict(reps=1001), dict(reps=''), dict(reps='1.2'),
                   dict(weight_kg=-1), dict(weight_kg='10000.01'), dict(weight_kg='NaN'),
                   dict(weight_kg='Infinity'), dict(weight_kg='1e2'), dict(weight_kg='1.001'),
                   dict(rir=-1), dict(rir=11), dict(rir='1.5')]
        edit = f'/workouts/{workout}/sets/{original[0][0]}/edit'
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                response = self.log_set(workout, set_number=2, **overrides) if 'set_number' not in overrides else self.log_set(workout, **overrides)
                self.assertEqual(response.status_code, 200)
                valid = dict(exercise_id=self.exercise, set_number=1, reps=8, weight_kg=0, rir=0)
                self.assertEqual(self.post(self.a, edit, **{**valid, **overrides}).status_code, 200)
                self.assertEqual(self.sets(workout), original)
        self.assertIn(b'already has a set', self.log_set(workout).data)
        self.assertEqual(self.sets(workout), original)
        self.log_set(workout, set_number=2)
        self.assertIn(b'already has a set', self.post(self.a, edit, exercise_id=self.exercise,
                                                    set_number=2, reps=10, weight_kg=20).data)
        self.assertEqual(self.sets(workout)[0], original[0])
        # The same set number is valid for another exercise and another session.
        self.assertEqual(self.log_set(workout, exercise_id=self.exercise+1).status_code, 302)
        other = self.start_workout()
        self.assertEqual(self.log_set(other).status_code, 302)

    def test_finish_is_idempotent_and_rejects_all_set_writes(self):
        workout = self.start_workout(); self.log_set(workout)
        original = self.sets(workout); base = f'/workouts/{workout}'
        self.post(self.a, base+'/finish')
        with self.connect_db() as conn:
            finished = conn.execute('SELECT finished_at FROM workout_session WHERE session_id=%s', (workout,)).fetchone()[0]
        self.assertIsNotNone(finished)
        self.assertEqual(self.post(self.a, base+'/finish').status_code, 302)
        for path in [base+'/sets/new', f'{base}/sets/{original[0][0]}/edit', f'{base}/sets/{original[0][0]}/delete']:
            self.assertEqual(self.post(self.a, path).status_code, 409)
        self.assertEqual(self.a.get(base+'/sets/new').status_code, 409)
        self.assertEqual(self.sets(workout), original)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT finished_at FROM workout_session WHERE session_id=%s', (workout,)).fetchone()[0], finished)

    def test_mutations_require_csrf_and_post_and_anonymous_access_is_protected(self):
        plan = self.create_plan(); day = self.create_day(plan)
        workout = self.start_workout(plan, day); self.log_set(workout)
        set_id = self.sets(workout)[0][0]; base = f'/workouts/{workout}'
        paths = [f'/workouts/start/{plan}/{day}', base+'/sets/new', f'{base}/sets/{set_id}/edit',
                 f'{base}/sets/{set_id}/delete', base+'/finish']
        for path in paths:
            self.assertEqual(self.a.post(path).status_code, 400, path)
            self.assertEqual(self.a.post(path, data={'csrf_token':'wrong'}).status_code, 400, path)
        for path in [paths[0], paths[3], paths[4]]:
            self.assertEqual(self.a.get(path).status_code, 405)
        anonymous = self.app.test_client()
        for path in ['/workouts', base, base+'/sets/new', f'{base}/sets/{set_id}/edit']:
            self.assertTrue(anonymous.get(path).location.endswith('/login'))
        for path in paths:
            self.assertTrue(self.post(anonymous, path).location.endswith('/login'))

    def test_plan_deletion_preserves_snapshot_and_legacy_exercise_names(self):
        for delete_day in [True, False]:
            with self.subTest(delete_day=delete_day):
                plan = self.create_plan('<script>Block</script>'); day = self.create_day(plan)
                self.create_entry(plan, day); workout = self.start_workout(plan, day); self.log_set(workout)
                with self.connect_db() as conn:
                    legacy = conn.execute('INSERT INTO workout_session(user_id,day_id,notes) VALUES (%s,%s,%s) RETURNING session_id',
                                          (self.owner, day, 'old notes')).fetchone()[0]
                    conn.execute('INSERT INTO set_log(session_id,exercise_id,set_number,reps) VALUES (%s,%s,1,8)', (legacy,self.exercise))
                path = f'/plans/{plan}/days/{day}/delete' if delete_day else f'/plans/{plan}/delete'
                self.assertEqual(self.post(self.a, path, confirm='delete').status_code, 302)
                for session_id in [workout, legacy]:
                    page = self.a.get(f'/workouts/{session_id}')
                    self.assertEqual(page.status_code, 200)
                    self.assertIn(b'Pull-up', page.data)
                page = self.a.get(f'/workouts/{workout}').data
                self.assertIn(b'Upper A', page)
                self.assertIn(b'&lt;script&gt;Block&lt;/script&gt;', page)
                self.assertNotIn(b'<script>', page)
                self.assertIn(b'6', page); self.assertIn(b'10', page)
                self.assertEqual(self.log_set(workout, set_number=2).status_code, 302)
                self.assertEqual(self.a.get('/workouts').status_code, 200)

    def test_completion_lock_blocks_concurrent_add_edit_and_delete(self):
        for mutation in ['add', 'edit', 'delete']:
            with self.subTest(mutation=mutation):
                workout = self.start_workout(); self.log_set(workout)
                original = self.sets(workout); set_id = original[0][0]
                path = {'add': f'/workouts/{workout}/sets/new',
                        'edit': f'/workouts/{workout}/sets/{set_id}/edit',
                        'delete': f'/workouts/{workout}/sets/{set_id}/delete'}[mutation]
                # Independent client/connection waits for a real completion transaction.
                client = self.app.test_client()
                with client.session_transaction() as state:
                    state['user_id'] = self.owner; state['csrf_token'] = 'concurrent-token'
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with self.connect_db() as finisher:
                        finisher.execute('SELECT session_id FROM workout_session WHERE session_id=%s FOR UPDATE', (workout,))
                        finisher.execute('UPDATE workout_session SET finished_at=clock_timestamp() WHERE session_id=%s', (workout,))
                        future = pool.submit(client.post, path, data=dict(csrf_token='concurrent-token',
                                             exercise_id=self.exercise, set_number=2, reps=10))
                        deadline = time.monotonic() + 5
                        blocked = False
                        while time.monotonic() < deadline:
                            with self.connect_db() as observer:
                                blocked = observer.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
                                    "AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query LIKE %s)",
                                    ('SELECT * FROM workout_session WHERE session_id=% FOR UPDATE',)).fetchone()[0]
                            if blocked or future.done():
                                break
                            time.sleep(0.02)
                    response = future.result(timeout=10)
                self.assertTrue(blocked, 'Mutation must wait for the session row lock')
                self.assertEqual(response.status_code, 409)
                self.assertEqual(self.sets(workout), original)
