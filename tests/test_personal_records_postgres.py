"""Strict, session-scoped PR badges and exact-load records from owned completed logs."""
from datetime import datetime,timedelta
from decimal import Decimal as D
import json
from unittest.mock import patch
from flask import template_rendered

from app.services import personal_records
from app.services.workout_progress import SNAPSHOT_PREFIX
from test_plans_postgres import PostgresTestCase


class PersonalRecordTests(PostgresTestCase):
    def session(self,when='2026-10-01T12:00:00+00:00',*,active=False,owner=None,notes='Old ordinary notes'):
        finished=datetime.fromisoformat(when)
        with self.connect_db() as conn:
            return conn.execute('INSERT INTO workout_session(user_id,started_at,finished_at,notes) VALUES (%s,%s,%s,%s) RETURNING session_id',
                (owner or self.owner,finished-timedelta(hours=1),None if active else finished,notes)).fetchone()[0]

    def log(self,session,weight,reps,number=1,exercise=None):
        with self.connect_db() as conn:
            return conn.execute('INSERT INTO set_log(session_id,exercise_id,set_number,weight_kg,reps) VALUES (%s,%s,%s,%s,%s) RETURNING set_id',
                (session,exercise or self.exercise,number,weight,reps)).fetchone()[0]

    def view(self,path):
        contexts=[]
        def capture(sender,template,context,**extra):contexts.append(context)
        with template_rendered.connected_to(capture,self.app):
            response=self.a.get(path)
        self.assertEqual(response.status_code,200)
        return response,contexts[-1]

    def badges(self,session):
        return self.view(f'/workouts/{session}')[1]['pr_badges']

    def kinds(self,badges):
        return {key:[row['kind'] for row in value] for key,value in badges.items()}

    def test_first_session_sets_are_baselines_in_every_category_not_prs(self):
        first=self.session()
        self.log(first,'20',20)
        self.log(first,'30',5,2)
        best=self.log(first,'30',6,3)
        self.log(first,None,10,4)
        body=self.log(first,None,15,5)
        response,context=self.view(f'/workouts/{first}')
        self.assertEqual(context['pr_badges'],{})
        self.assertNotIn(b'class="new-pr-badge"',response.data)
        _,context=self.view(f'/workouts/progress?exercise_id={self.exercise}')
        self.assertEqual(context['records']['heaviest']['set_id'],best)
        self.assertEqual(context['records']['bodyweight']['set_id'],body)

    def test_heaviest_load_awards_only_best_current_set_with_deterministic_ties(self):
        before=self.session();self.log(before,'40',8)
        current=self.session('2026-10-02T12:00:00+00:00')
        self.log(current,'45',8)
        self.log(current,'50',4,2)
        winner=self.log(current,'50',6,3)
        self.log(current,'50',6,4)
        self.assertEqual(self.kinds(self.badges(current)),{winner:['heaviest']})

    def test_rep_pr_uses_same_exact_load_and_only_the_best_set(self):
        before=self.session();self.log(before,'50',8)
        current=self.session('2026-10-02T12:00:00+00:00')
        self.log(current,'50',9)
        winner=self.log(current,'50.00',12,2)
        self.log(current,'50',12,3)
        self.log(current,'50',10,4)
        self.assertEqual(self.kinds(self.badges(current)),{winner:['load_reps']})
        self.assertEqual(self.badges(current)[winner][0]['weight_kg'],D('50'))
        tie=self.session('2026-10-03T12:00:00+00:00');self.log(tie,'50',12)
        self.assertEqual(self.badges(tie),{})

    def test_new_load_is_a_rep_baseline_and_is_not_compared_to_other_loads(self):
        before=self.session();self.log(before,'50',5)
        new=self.session('2026-10-02T12:00:00+00:00')
        self.log(new,'25',30);self.log(new,'25',35,2)
        self.assertEqual(self.badges(new),{})
        next_session=self.session('2026-10-03T12:00:00+00:00')
        winner=self.log(next_session,'25',36)
        nearby=self.log(next_session,'25.01',100,2)
        self.assertEqual(self.kinds(self.badges(next_session)),{winner:['load_reps']})
        self.assertNotIn(nearby,self.badges(next_session))

    def test_bodyweight_pr_is_separate_and_only_best_unweighted_set_is_badged(self):
        before=self.session();self.log(before,None,10);self.log(before,'20',20,2)
        current=self.session('2026-10-02T12:00:00+00:00')
        self.log(current,None,11)
        winner=self.log(current,None,15,2)
        self.log(current,None,15,3)
        self.log(current,'20',20,4)
        self.assertEqual(self.kinds(self.badges(current)),{winner:['bodyweight_reps']})
        tie=self.session('2026-10-03T12:00:00+00:00');self.log(tie,None,15)
        self.assertEqual(self.badges(tie),{})

    def test_zero_load_and_zero_reps_are_real_baselines_not_bodyweight_or_missing(self):
        before=self.session();self.log(before,'0',0);self.log(before,None,0,2)
        current=self.session('2026-10-02T12:00:00+00:00')
        weighted=self.log(current,'0',1)
        body=self.log(current,None,1,2)
        self.assertEqual(self.kinds(self.badges(current)),{weighted:['load_reps'],body:['bodyweight_reps']})
        _,context=self.view(f'/workouts/progress?exercise_id={self.exercise}')
        self.assertEqual(context['records']['heaviest']['weight_kg'],D(0))
        self.assertEqual(context['records']['bodyweight']['weight_kg'],None)

    def test_first_weighted_and_bodyweight_results_have_independent_baselines(self):
        before=self.session();self.log(before,None,50)
        first_weight=self.session('2026-10-02T12:00:00+00:00');self.log(first_weight,'10',60)
        self.assertEqual(self.badges(first_weight),{})
        with self.connect_db() as conn:
            another=conn.execute("SELECT exercise_id FROM exercise WHERE tracking_type='reps' AND exercise_id<>%s ORDER BY exercise_id LIMIT 1",(self.exercise,)).fetchone()[0]
        self.log(before,'10',100,exercise=another)
        self.log(first_weight,None,20,exercise=another)
        self.assertEqual(self.badges(first_weight),{})

    def test_historical_badges_ignore_later_completions_active_sessions_and_start_order(self):
        before=self.session();self.log(before,'40',8)
        current=self.session('2026-10-02T12:00:00+00:00');winner=self.log(current,'50',5)
        later=self.session('2026-10-03T12:00:00+00:00');self.log(later,'100',20)
        ongoing=self.session('2026-10-01T11:00:00+00:00',active=True);self.log(ongoing,'999',99)
        with self.connect_db() as conn:
            conn.execute("UPDATE workout_session SET started_at='2026-09-01' WHERE session_id=%s",(later,))
        self.assertEqual(self.kinds(self.badges(current)),{winner:['heaviest']})
        self.assertEqual(self.badges(ongoing),{})

    def test_same_completion_timestamp_uses_session_id_and_summary_keeps_first_exact_tie(self):
        first=self.session();first_set=self.log(first,'20',10)
        second=self.session();self.log(second,'20',10)
        self.assertEqual(self.badges(second),{})
        _,context=self.view(f'/workouts/progress?exercise_id={self.exercise}')
        self.assertEqual(context['records']['heaviest']['set_id'],first_set)
        third=self.session();improvement=self.log(third,'20',11)
        self.assertEqual(self.kinds(self.badges(third)),{improvement:['load_reps']})
        fourth=self.session();self.log(fourth,'25',5)
        self.assertEqual(next(iter(self.kinds(self.badges(fourth)).values())),['heaviest'])

    def test_progress_records_read_all_loads_and_bodyweight_without_replacing_chart_or_history(self):
        before=self.session();self.log(before,'50',5);self.log(before,'40',15,2)
        current=self.session('2026-10-02T12:00:00+00:00')
        heaviest=self.log(current,'55',3)
        reps=self.log(current,'40',16,2)
        body=self.log(current,None,20,3)
        response,context=self.view(f'/workouts/progress?exercise_id={self.exercise}')
        self.assertEqual(context['records']['heaviest']['set_id'],heaviest)
        self.assertEqual([(row['weight_kg'],row['reps']) for row in context['records']['by_load']],[(D(55),3),(D(50),5),(D(40),16)])
        self.assertEqual(context['records']['bodyweight']['set_id'],body)
        self.assertEqual(len(context['points']),2)
        self.assertIn(b'data-progress-session-id=',response.data)
        self.assertIn(b'progress-chart-title',response.data)
        self.assertEqual(self.kinds(self.badges(current)),{heaviest:['heaviest'],reps:['load_reps']})  # First bodyweight result is a baseline.

    def test_finish_and_deleted_plans_preserve_badges_and_valid_legacy_logged_ids(self):
        before=self.session();self.log(before,'20',5)
        plan=self.create_plan('Deleted PR block');day=self.create_day(plan,'PR day');self.create_entry(plan,day)
        current=int(self.post(self.a,f'/workouts/start/{plan}/{day}').location.rsplit('/',1)[1])
        self.post(self.a,f'/workouts/{current}/sets/new',exercise_id=self.exercise,set_number=1,weight_kg='30.25',reps=8)
        self.assertEqual(self.badges(current),{})
        self.assertEqual(self.post(self.a,f'/workouts/{current}/finish').status_code,302)
        earned=self.badges(current)
        self.assertEqual(list(self.kinds(earned).values()),[['heaviest']])
        self.post(self.a,f'/plans/{plan}/delete',confirm='delete')
        response,_=self.view(f'/workouts/{current}')
        self.assertEqual(self.badges(current),earned)
        self.assertIn(b'Deleted PR block',response.data)
        legacy=SNAPSHOT_PREFIX+json.dumps(dict(plan_name='Legacy',day_name='No ID',exercises=[dict(name='Same name',target_sets=3,reps_min=6,reps_max=10)]))
        empty=self.session('2026-10-02T12:00:00+00:00',notes=legacy)
        self.assertEqual(self.badges(empty),{})
        valid=self.log(empty,'25',6)
        self.assertEqual(self.kinds(self.badges(empty)),{valid:['heaviest']})

    def test_records_enforce_ownership_supported_types_and_bulk_read_only_derivation(self):
        with self.b.session_transaction() as state:other=state['user_id']
        baseline=self.session();self.log(baseline,'20',5)
        outsider=self.session(owner=other);self.log(outsider,'999',100)
        current=self.session('2026-10-02T12:00:00+00:00');winner=self.log(current,'30',5)
        with self.connect_db() as conn:
            timed=conn.execute("SELECT exercise_id FROM exercise WHERE tracking_type='duration' ORDER BY exercise_id LIMIT 1").fetchone()[0]
            foreign=conn.execute("SELECT exercise_id FROM exercise WHERE tracking_type='reps' AND exercise_id<>%s ORDER BY exercise_id LIMIT 1",(self.exercise,)).fetchone()[0]
        self.log(outsider,'90',10,exercise=foreign)
        timed_session=self.session('2026-10-03T12:00:00+00:00');self.log(timed_session,None,40,exercise=timed)
        with patch('app.services.personal_records.query',wraps=personal_records.query) as read:
            self.assertEqual(self.kinds(self.badges(current)),{winner:['heaviest']})
        self.assertEqual(read.call_count,1)
        self.assertEqual(self.badges(timed_session),{})
        self.assertEqual(self.b.get(f'/workouts/{current}').status_code,404)
        self.assertEqual(self.a.get(f'/workouts/progress?exercise_id={foreign}&user_id={other}').status_code,404)
        _,context=self.view(f'/workouts/progress?exercise_id={self.exercise}&user_id={other}')
        self.assertEqual(context['records']['heaviest']['weight_kg'],D(30))
        with self.connect_db() as conn:
            before=(conn.execute('SELECT * FROM workout_session ORDER BY session_id').fetchall(),
                    conn.execute('SELECT * FROM set_log ORDER BY set_id').fetchall(),
                    conn.execute('SELECT * FROM schema_migration ORDER BY version').fetchall())
        self.badges(current);self.view('/workouts/progress')
        with self.connect_db() as conn:
            after=(conn.execute('SELECT * FROM workout_session ORDER BY session_id').fetchall(),
                   conn.execute('SELECT * FROM set_log ORDER BY set_id').fetchall(),
                   conn.execute('SELECT * FROM schema_migration ORDER BY version').fetchall())
        self.assertEqual(after,before)
