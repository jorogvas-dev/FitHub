"""Owned completed-session selection, bulk references and historical progress."""
from datetime import datetime, timedelta
from decimal import Decimal as D
import json
from unittest.mock import patch
from flask import template_rendered

from app.services import workout_progress as progress
from test_plans_postgres import PostgresTestCase


class WorkoutProgressTests(PostgresTestCase):
    def session(self, when='2026-10-04T12:00:00+00:00', *, active=False, owner=None, day=None, notes=None):
        finished=datetime.fromisoformat(when)
        with self.connect_db() as conn:
            return conn.execute('INSERT INTO workout_session(user_id,day_id,started_at,finished_at,notes) '
                'VALUES (%s,%s,%s,%s,%s) RETURNING session_id',
                (owner or self.owner,day,finished-timedelta(hours=1),None if active else finished,notes or 'Older ordinary notes')).fetchone()[0]

    def log(self, session, number=1, weight='20', reps=8, exercise=None):
        with self.connect_db() as conn:
            return conn.execute('INSERT INTO set_log(session_id,exercise_id,set_number,reps,weight_kg) '
                'VALUES (%s,%s,%s,%s,%s) RETURNING set_id',
                (session,exercise or self.exercise,number,reps,weight)).fetchone()[0]

    def active(self):
        plan=self.create_plan('Current plan')
        day=self.create_day(plan,'Current day')
        self.create_entry(plan,day)
        response=self.post(self.a,f'/workouts/start/{plan}/{day}')
        return int(response.location.rsplit('/',1)[1])

    def context(self,path):
        captured=[]
        def record(sender,template,context,**extra):captured.append(context)
        with template_rendered.connected_to(record,self.app):
            response=self.a.get(path)
        self.assertEqual(response.status_code,200)
        return response,captured[-1]

    def test_references_choose_latest_completed_session_and_return_all_sets_in_order_in_one_query(self):
        old=self.session('2026-10-01T12:00:00+00:00');self.log(old,weight='999')
        latest=self.session('2026-10-05T12:00:00+00:00')
        second=self.log(latest,number=3,weight='37.50',reps=6)
        first=self.log(latest,number=1,weight=None,reps=12)
        self.session('2026-10-08T12:00:00+00:00')  # A later completion without this exercise is not a match.
        active_other=self.session('2026-10-09T12:00:00+00:00',active=True);self.log(active_other,weight='888')
        active=self.active()
        with self.connect_db() as conn:
            other=conn.execute("SELECT exercise_id FROM exercise WHERE tracking_type='reps' AND exercise_id<>%s ORDER BY exercise_id LIMIT 1",(self.exercise,)).fetchone()[0]
        self.log(active,exercise=other)
        previous_other=self.session('2026-10-02T12:00:00+00:00');self.log(previous_other,exercise=other,weight=12)
        with patch('app.services.workout_progress.query',wraps=progress.query) as read:
            response,context=self.context(f'/workouts/{active}')
        self.assertEqual(read.call_count,1)
        self.assertEqual(set(read.call_args.args[2][1]),{self.exercise,other})
        reference=context['references'][self.exercise]
        self.assertEqual(reference['session_id'],latest)
        self.assertEqual([row['set_id'] for row in reference['sets']],[first,second])
        self.assertIn(b'37.50 kg',response.data)
        self.assertIn(b'Bodyweight',response.data)
        self.assertNotIn(b'999.00 kg',response.data)
        self.assertNotIn(b'888.00 kg',response.data)
        self.assertEqual(self.b.get(f'/workouts/{active}').status_code,404)

    def test_completion_time_and_session_id_break_ties_even_when_start_order_differs(self):
        one=self.session('2026-10-06T12:00:00+00:00');self.log(one,weight=10)
        two=self.session('2026-10-06T12:00:00+00:00');self.log(two,weight=20)
        with self.connect_db() as conn:
            conn.execute("UPDATE workout_session SET started_at='2026-10-01' WHERE session_id=%s",(two,))
        _,context=self.context(f'/workouts/{self.active()}')
        self.assertEqual(context['references'][self.exercise]['session_id'],two)
        with self.connect_db() as conn:
            conn.execute("UPDATE workout_session SET finished_at='2026-10-07' WHERE session_id=%s",(one,))
        _,context=self.context(f'/workouts/{self.active()}')
        self.assertEqual(context['references'][self.exercise]['session_id'],one)

    def test_cross_plan_reference_and_progress_survive_deleted_plan_and_catalogue_rename(self):
        plan=self.create_plan('Deleted strength block');day=self.create_day(plan,'Original upper')
        self.create_entry(plan,day)
        previous=int(self.post(self.a,f'/workouts/start/{plan}/{day}').location.rsplit('/',1)[1])
        self.log(previous,weight='42.75',reps=10)
        self.post(self.a,f'/workouts/{previous}/finish')
        self.post(self.a,f'/plans/{plan}/delete',confirm='delete')
        with self.connect_db() as conn:
            name=conn.execute('SELECT name FROM exercise WHERE exercise_id=%s',(self.exercise,)).fetchone()[0]
            conn.execute('UPDATE exercise SET name=%s,is_active=false WHERE exercise_id=%s',('Renamed historical exercise',self.exercise))
        try:
            current=self.session(active=True,notes=progress.SNAPSHOT_PREFIX+json.dumps(dict(plan_name='Other plan',day_name='Other day',exercises=[
                dict(exercise_id=self.exercise,name=name,tracking_type='reps',target_sets=3,reps_min=6,reps_max=10)])))
            response,context=self.context(f'/workouts/{current}')
            self.assertEqual(context['references'][self.exercise]['session_id'],previous)
            response,context=self.context(f'/workouts/progress?exercise_id={self.exercise}')
            self.assertEqual(len(context['points']),1)
            self.assertIn(b'Deleted strength block',response.data)
            self.assertIn(b'Original upper',response.data)
            self.assertEqual(context['points'][0]['weight_kg'],D('42.75'))
        finally:
            with self.connect_db() as conn:
                conn.execute('UPDATE exercise SET name=%s,is_active=true WHERE exercise_id=%s',(name,self.exercise))

    def test_heaviest_set_uses_higher_reps_on_weight_ties_and_keeps_same_day_sessions(self):
        first=self.session('2026-10-04T10:00:00+00:00')
        self.log(first,number=1,weight='40.25',reps=15)
        self.log(first,number=2,weight='50.50',reps=6)
        chosen=self.log(first,number=3,weight='50.50',reps=10)
        self.log(first,number=4,weight='50.50',reps=10)
        second=self.session('2026-10-04T18:00:00+00:00');self.log(second,weight='55.25',reps=5)
        ongoing=self.session('2026-10-05T12:00:00+00:00',active=True);self.log(ongoing,weight='999')
        response,context=self.context(f'/workouts/progress?exercise_id={self.exercise}')
        self.assertEqual([row['session_id'] for row in context['points']],[first,second])
        self.assertEqual(context['points'][0]['set_id'],chosen)
        self.assertEqual(context['points'][0]['reps'],10)
        self.assertEqual(response.data.count(b'data-progress-session-id='),2)
        self.assertEqual(response.data.count(b'<circle class="chart-point"'),2)
        xs=[point['x'] for point in context['chart']['tracks'][0]['points']]
        self.assertEqual(len(set(xs)),2)

    def test_bodyweight_and_explicit_zero_are_distinct_and_supported(self):
        body=self.session('2026-10-01T12:00:00+00:00')
        self.log(body,weight=None,reps=6)
        best=self.log(body,number=2,weight=None,reps=14)
        zero=self.session('2026-10-02T12:00:00+00:00')
        self.log(zero,weight=None,reps=100)
        recorded=self.log(zero,number=2,weight='0',reps=0)
        response,context=self.context(f'/workouts/progress?exercise_id={self.exercise}')
        self.assertEqual([row['set_id'] for row in context['points']],[best,recorded])
        self.assertIsNone(context['points'][0]['weight_kg'])
        self.assertEqual(context['points'][1]['weight_kg'],D('0'))
        self.assertEqual([track['label'] for track in context['chart']['tracks']],['Recorded weight (kg)','Bodyweight sets (reps)'])
        self.assertIn(b'Bodyweight',response.data)
        self.assertIn(b'>0.00</td>',response.data)

    def test_empty_single_session_and_snapshot_only_history(self):
        response,context=self.context('/workouts/progress')
        self.assertEqual(context['points'],[])
        self.assertIn(b'No completed exercise history yet',response.data)
        self.assertNotIn(b'<circle class="chart-point"',response.data)
        snapshot=progress.SNAPSHOT_PREFIX+json.dumps(dict(plan_name='Old plan',day_name='Old day',exercises=[dict(name='Pull-up',target_sets=4,reps_min=6,reps_max=10)]))
        self.session(notes=snapshot)
        self.assertEqual(self.a.get(f'/workouts/progress?exercise_id={self.exercise}').status_code,404)
        complete=self.session();self.log(complete,weight='12.50')
        response,context=self.context('/workouts/progress')
        self.assertEqual(len(context['points']),1)
        self.assertIn(b'One session so far',response.data)
        self.assertEqual(response.data.count(b'<circle class="chart-point"'),1)
        self.assertEqual(context['chart']['tracks'][0]['segments'],[])

    def test_missing_snapshot_ids_are_not_matched_by_name_and_do_not_break_logger(self):
        previous=self.session();self.log(previous,weight='77.25')
        with self.connect_db() as conn:
            name=conn.execute('SELECT name FROM exercise WHERE exercise_id=%s',(self.exercise,)).fetchone()[0]
        snapshot=progress.SNAPSHOT_PREFIX+json.dumps(dict(plan_name='Legacy',day_name='Missing ID',exercises=[
            dict(name=name,target_sets=3,reps_min=6,reps_max=10),dict(name=name,exercise_id=None,target_sets=2,reps_min=8,reps_max=12)]))
        active=self.session(active=True,notes=snapshot)
        response,context=self.context(f'/workouts/{active}')
        self.assertEqual(context['references'],{})
        self.assertEqual(response.data.count(b'No previous sets'),2)
        self.assertNotIn(b'77.25',response.data)
        self.assertEqual(self.a.get(f'/workouts/{active}/sets/new').status_code,200)
        self.log(active,weight='5')
        response,context=self.context(f'/workouts/{active}')
        self.assertEqual(len(context['cards']),3)
        self.assertEqual(context['references'][self.exercise]['session_id'],previous)
        self.assertEqual(response.data.count(b'No previous sets'),2)

    def test_legacy_notes_with_valid_logged_ids_still_provide_progress(self):
        old=self.session(notes='Ordinary older notes');self.log(old,weight='15.75')
        response,context=self.context('/workouts/progress')
        self.assertEqual([row['session_id'] for row in context['points']],[old])
        self.assertIn(b'Original training day unavailable',response.data)

    def test_picker_and_direct_requests_enforce_ownership_and_supported_types(self):
        with self.b.session_transaction() as state:other_owner=state['user_id']
        with self.connect_db() as conn:
            foreign=conn.execute("SELECT exercise_id FROM exercise WHERE tracking_type='reps' AND exercise_id<>%s ORDER BY exercise_id LIMIT 1",(self.exercise,)).fetchone()[0]
            timed=conn.execute("SELECT exercise_id FROM exercise WHERE tracking_type='duration' ORDER BY exercise_id LIMIT 1").fetchone()[0]
        own=self.session();self.log(own)
        other=self.session(owner=other_owner);self.log(other,exercise=foreign,weight='987.65')
        self.log(other,number=2,weight='999.25')
        legacy_timed=self.session();self.log(legacy_timed,exercise=timed,reps=30)
        active=self.session(active=True);self.log(active,exercise=foreign)
        response,context=self.context('/workouts/progress')
        self.assertEqual([row['exercise_id'] for row in context['exercises']],[self.exercise])
        self.assertNotIn(b'999.25',response.data)
        self.assertEqual(self.a.get(f'/workouts/progress?exercise_id={foreign}&user_id={other_owner}').status_code,404)
        self.assertEqual(self.a.get(f'/workouts/progress?exercise_id={timed}').status_code,404)
        self.assertEqual(self.b.get(f'/workouts/progress?exercise_id={self.exercise}').status_code,200)  # Bob also logged this ID.
        current=self.active()
        _,context=self.context(f'/workouts/{current}')
        self.assertEqual(context['references'][self.exercise]['session_id'],own)
        for raw in ('0','-1','abc','9223372036854775808'):
            self.assertEqual(self.a.get('/workouts/progress?exercise_id='+raw).status_code,400)
        self.assertTrue(self.app.test_client().get('/workouts/progress').location.endswith('/login'))

    def test_read_views_do_not_modify_sessions_sets_or_schema_ledger(self):
        previous=self.session();self.log(previous)
        current=self.active()
        with self.connect_db() as conn:
            before=(conn.execute('SELECT * FROM workout_session ORDER BY session_id').fetchall(),
                    conn.execute('SELECT * FROM set_log ORDER BY set_id').fetchall(),
                    conn.execute('SELECT * FROM schema_migration ORDER BY version').fetchall())
        self.a.get(f'/workouts/{current}');self.a.get('/workouts/progress')
        self.assertNotIn(b'Last completed session',self.a.get(f'/workouts/{previous}').data)
        with self.connect_db() as conn:
            after=(conn.execute('SELECT * FROM workout_session ORDER BY session_id').fetchall(),
                   conn.execute('SELECT * FROM set_log ORDER BY set_id').fetchall(),
                   conn.execute('SELECT * FROM schema_migration ORDER BY version').fetchall())
        self.assertEqual(after,before)
