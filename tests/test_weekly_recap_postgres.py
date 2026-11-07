"""Timezone-local weekly summaries from real PostgreSQL, using shared PR rules."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from unittest.mock import patch

from flask import template_rendered

from app.services import weekly_recap as recap_service
from test_plans_postgres import PostgresTestCase


class WeeklyRecapTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        clock = patch('app.services.nutrition.utc_now',
                      return_value=datetime(2026, 11, 4, 12, tzinfo=timezone.utc))
        clock.start()
        self.addCleanup(clock.stop)

    def session(self, when, *, owner=None, active=False):
        finish = datetime.fromisoformat(when)
        with self.connect_db() as conn:
            return conn.execute('INSERT INTO workout_session(user_id,started_at,finished_at,notes) '
                                'VALUES (%s,%s,%s,%s) RETURNING session_id',
                                (owner or self.owner, finish-timedelta(hours=2),
                                 None if active else finish, 'Legacy notes, no plan needed')).fetchone()[0]

    def log(self, session, weight, reps, number=1, exercise=None):
        with self.connect_db() as conn:
            return conn.execute('INSERT INTO set_log(session_id,exercise_id,set_number,weight_kg,reps) '
                                'VALUES (%s,%s,%s,%s,%s) RETURNING set_id',
                                (session, exercise or self.exercise, number, weight, reps)).fetchone()[0]

    def weight(self, when, amount, owner=None):
        with self.connect_db() as conn:
            conn.execute('INSERT INTO body_measurement(user_id,measured_on,weight_kg) VALUES (%s,%s,%s)',
                         (owner or self.owner, when, amount))

    def dashboard(self, suffix='', client=None):
        contexts = []
        def capture(sender, template, context, **extra):
            contexts.append(context)
        with template_rendered.connected_to(capture, self.app):
            response = (client or self.a).get('/dashboard'+suffix)
        self.assertEqual(response.status_code, 200)
        return response, contexts[-1]['recap']

    def test_sunday_monday_boundaries_default_and_saved_timezone_completion_not_start(self):
        # Sofia Monday begins at Sunday 22:00 UTC in November.
        sunday = self.session('2026-11-01T21:59:59+00:00')
        monday = self.session('2026-11-01T22:00:00+00:00')
        self.session('2026-11-08T21:59:59+00:00')
        self.session('2026-11-08T22:00:00+00:00')  # Next Monday, excluded.
        self.session('2026-11-04T12:00:00+00:00', active=True)
        response, recap = self.dashboard()
        self.assertEqual(recap['timezone'], 'Europe/Sofia')
        self.assertEqual((recap['start'], recap['last_day']), (date(2026,11,2), date(2026,11,8)))
        self.assertEqual(recap['completed'], 2)
        self.assertEqual(self.dashboard('?week=last')[1]['completed'], 1)
        self.assertIn(b'02 Nov 2026', response.data)
        self.assertIn(b'08 Nov 2026', response.data)
        with patch('app.services.nutrition.utc_now', return_value=datetime(2026,11,1,21,59,59,tzinfo=timezone.utc)):
            self.assertEqual(self.dashboard()[1]['start'], date(2026,10,26))
        with patch('app.services.nutrition.utc_now', return_value=datetime(2026,11,1,22,tzinfo=timezone.utc)):
            self.assertEqual(self.dashboard()[1]['start'], date(2026,11,2))
        self.assertEqual(self.post(self.a, '/nutrition/settings', timezone='America/Los_Angeles').status_code, 302)
        self.session('2026-11-02T07:59:59+00:00')  # Sunday in saved timezone.
        self.session('2026-11-02T08:00:00+00:00')  # Monday in saved timezone.
        with patch('app.services.nutrition.utc_now', return_value=datetime(2026,11,2,7,59,59,tzinfo=timezone.utc)):
            self.assertEqual(self.dashboard()[1]['start'], date(2026,10,26))
        with patch('app.services.nutrition.utc_now', return_value=datetime(2026,11,2,8,tzinfo=timezone.utc)):
            _, recap = self.dashboard()
            self.assertEqual(recap['start'], date(2026,11,2))
            self.assertEqual(recap['completed'], 3)  # Saved-zone Monday + Nov 8 + Nov 9 UTC.
            self.assertEqual(self.dashboard('?week=last')[1]['completed'], 3)
        # These were completed sessions whose start times fall on the prior day.
        self.assertNotEqual(sunday, monday)

    def test_dst_weeks_use_two_local_midnights_instead_of_168_hours(self):
        spring = recap_service.week_bounds(date(2026,3,25), 'Europe/Sofia')
        autumn = recap_service.week_bounds(date(2026,10,22), 'Europe/Sofia')
        self.assertEqual(spring['end_utc']-spring['start_utc'], timedelta(hours=167))
        self.assertEqual(autumn['end_utc']-autumn['start_utc'], timedelta(hours=169))
        self.assertEqual(spring['start'], date(2026,3,23))

    def test_records_reuse_all_earlier_baselines_and_update_only_after_whole_session(self):
        baseline = self.session('2026-10-25T12:00:00+00:00')
        self.log(baseline, '50', 5)
        self.log(baseline, '40', 8, 2)
        self.log(baseline, None, 10, 3)
        active = self.session('2026-10-26T12:00:00+00:00', active=True)
        self.log(active, '999', 999)
        # Equal completion instants use session IDs, just as the completed view does.
        first = self.session('2026-11-02T12:00:00+00:00')
        self.log(first, '55', 4)
        self.log(first, '40', 9, 2)
        self.log(first, '40', 10, 3)  # Only best at this load earns a record.
        self.log(first, None, 12, 4)
        second = self.session('2026-11-02T12:00:00+00:00')
        self.log(second, '55', 6)
        self.log(second, None, 12, 2)  # Tie: no new record.
        third = self.session('2026-11-03T12:00:00+00:00')
        self.log(third, '55', 6)
        future = self.session('2026-11-09T12:00:00+00:00')
        self.log(future, '1000', 999)
        with patch('app.services.weekly_recap.query', wraps=recap_service.query) as reads:
            response, recap = self.dashboard()
        self.assertEqual((recap['completed'], recap['new_records']), (3,4))
        self.assertEqual(reads.call_count, 3)
        self.assertIn(b'New records', response.data)
        # The recap and the existing completed-workout badges must agree.
        count = []
        def capture(sender, template, context, **extra):
            if 'pr_badges' in context:
                count.append(sum(len(reasons) for reasons in context['pr_badges'].values()))
        with template_rendered.connected_to(capture, self.app):
            for identifier in [first,second,third]:
                self.assertEqual(self.a.get(f'/workouts/{identifier}').status_code, 200)
        self.assertEqual(sum(count), recap['new_records'])

    def test_first_results_are_baselines_and_unsupported_sets_are_not_records(self):
        first = self.session('2026-11-02T12:00:00+00:00')
        self.log(first, '20', 5)
        self.log(first, '30', 10, 2)
        self.log(first, None, 10, 3)
        with self.connect_db() as conn:
            timed = conn.execute("SELECT exercise_id FROM exercise WHERE tracking_type='duration' LIMIT 1").fetchone()[0]
        before = self.session('2026-10-25T12:00:00+00:00')
        self.log(before, None, 10, exercise=timed)
        current = self.session('2026-11-03T12:00:00+00:00')
        self.log(current, None, 20, exercise=timed)
        self.session('2026-11-04T12:00:00+00:00')  # Completed without sets counts too.
        _, recap = self.dashboard()
        self.assertEqual((recap['completed'], recap['new_records']), (3,0))

    def test_counts_categories_even_when_shared_rule_returns_two_on_one_set(self):
        current = self.session('2026-11-02T12:00:00+00:00')
        identifier = self.log(current, '30', 10)
        # The aggregation contract counts distinct reasons, not badged sets.
        with patch('app.services.personal_records.badges_from_sets',
                   return_value={identifier: [dict(kind='heaviest'),dict(kind='load_reps')]}):
            self.assertEqual(self.dashboard()[1]['new_records'], 2)

    def test_zero_one_multiple_weigh_ins_use_only_the_selected_week_and_exact_precision(self):
        self.weight('2026-11-01', '90')
        self.weight('2026-11-09', '60')
        response, recap = self.dashboard()
        self.assertEqual(recap['weight_count'], 0)
        self.assertIsNone(recap['weight_change'])
        self.assertIn(b'No weigh-ins this week.', response.data)
        self.assertIn(b'href="/bodyweight/new">Add weight', response.data)
        self.weight('2026-11-04', '78.35')
        response, recap = self.dashboard()
        self.assertEqual(recap['weight_count'], 1)
        self.assertIsNone(recap['weight_change'])
        self.assertIn(b'78.35', response.data)
        self.assertIn(b'Two weigh-ins within this week are needed', response.data)
        self.weight('2026-11-02', '78.50')
        self.weight('2026-11-08', '78.10')
        response, recap = self.dashboard()
        self.assertEqual(recap['weight_change'], D('-0.40'))
        self.assertEqual(recap['first_weight']['measured_on'], date(2026,11,2))
        self.assertEqual(recap['last_weight']['measured_on'], date(2026,11,8))
        self.assertIn(b'-0.40', response.data)
        self.assertEqual(self.dashboard('?week=last')[1]['weight_count'], 1)

    def test_zero_weight_change_is_not_missing_and_positive_change_has_sign(self):
        self.weight('2026-11-02', '80')
        self.weight('2026-11-04', '80')
        response, recap = self.dashboard()
        self.assertEqual(recap['weight_change'], D('0'))
        self.assertIn(b'data-recap="weight-change">0.00', response.data)
        with self.connect_db() as conn:
            conn.execute("UPDATE body_measurement SET weight_kg=80.25 WHERE user_id=%s AND measured_on='2026-11-04'", (self.owner,))
        self.assertIn(b'data-recap="weight-change">+0.25', self.dashboard()[0].data)

    def test_account_isolation_forged_parameters_and_read_only_bulk_queries(self):
        with self.b.session_transaction() as state:
            other = state['user_id']
        old = self.session('2026-10-25T12:00:00+00:00')
        self.log(old, '20', 5)
        current = self.session('2026-11-02T12:00:00+00:00')
        self.log(current, '25', 5)
        foreign = self.session('2026-10-25T12:00:00+00:00', owner=other)
        self.log(foreign, '999', 999)
        self.session('2026-11-02T12:00:00+00:00', owner=other)
        self.weight('2026-11-02', '499.99', owner=other)
        with self.connect_db() as conn:
            before = {table: conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()
                      for table in ['workout_session','set_log','body_measurement','nutrition_settings']}
        with patch('app.services.weekly_recap.query', wraps=recap_service.query) as reads:
            response, recap = self.dashboard(f'?user_id={other}&exercise_id=999&week=this')
        self.assertEqual((recap['completed'],recap['new_records'],recap['weight_count']), (1,1,0))
        self.assertEqual(reads.call_count, 3)
        self.assertNotIn(b'499.99', response.data)
        self.assertEqual(self.dashboard(client=self.b)[1]['new_records'], 0)
        self.assertEqual(self.dashboard('?week=invalid')[1]['selection'], 'this')
        self.assertEqual(self.app.test_client().get('/dashboard').status_code, 302)
        with self.connect_db() as conn:
            for table, rows in before.items():
                self.assertEqual(conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall(), rows)
