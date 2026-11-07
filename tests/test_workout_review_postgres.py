"""Private, read-only AI review with real PostgreSQL and mocked inference."""
from datetime import datetime,timedelta
import json
from unittest.mock import patch

from flask import template_rendered

from app.services import workout_review, workout_ai
from app.services.workout_progress import SNAPSHOT_PREFIX
from test_plans_postgres import PostgresTestCase


class WorkoutReviewTests(PostgresTestCase):
    def snapshot(self, **changes):
        item=dict(exercise_id=self.exercise,name='Snapshot exercise',tracking_type='reps',target_sets=2,reps_min=6,reps_max=12)
        item.update(changes)
        return SNAPSHOT_PREFIX+json.dumps(dict(plan_name='Original plan',day_name='Original day',exercises=[item]))

    def session(self, when='2026-11-02T12:00:00+00:00', *, owner=None, notes=None, active=False):
        finished=datetime.fromisoformat(when)
        with self.connect_db() as conn:
            return conn.execute('INSERT INTO workout_session(user_id,started_at,finished_at,notes) '
                'VALUES (%s,%s,%s,%s) RETURNING session_id',
                (owner or self.owner,finished-timedelta(hours=1),None if active else finished,
                 self.snapshot() if notes is None else notes)).fetchone()[0]

    def log(self, session, weight, reps, number=1, exercise=None):
        with self.connect_db() as conn:
            return conn.execute('INSERT INTO set_log(session_id,exercise_id,set_number,weight_kg,reps) '
                'VALUES (%s,%s,%s,%s,%s) RETURNING set_id',
                (session,exercise or self.exercise,number,weight,reps)).fetchone()[0]

    def data(self, session):
        with self.connect_db() as conn:
            from app.routes.plans import query
            row=query(conn,'SELECT * FROM workout_session WHERE session_id=%s',(session,),one=True)
            logs=query(conn,'SELECT l.*,e.name,e.tracking_type,e.is_active FROM set_log l JOIN exercise e USING(exercise_id) WHERE session_id=%s',(session,))
            badges=workout_review.personal_records.workout_badges(conn,self.owner,row,logs)
            return workout_review.build_facts(conn,self.owner,row,logs,badges)

    def review(self, identifier, client=None):
        contexts=[]
        def capture(sender,template,context,**extra):contexts.append(context)
        with template_rendered.connected_to(capture,self.app):
            response=self.post(client or self.a,f'/workouts/{identifier}/review')
        return response,contexts[-1] if contexts else None

    def choose(self, data, schema):
        return json.dumps(dict(highlights=[row['id'] for row in data['facts'][:6]],suggestions=[
            dict(target_id=row['id'],explanation_id='consistency') for row in data['targets'][:3]]))

    def test_get_never_generates_post_requires_csrf_completed_owner_and_authentication(self):
        current=self.session(); self.log(current,'40',8)
        active=self.session(active=True)
        with patch('app.services.workout_ai.review',side_effect=self.choose) as provider:
            page=self.a.get(f'/workouts/{current}')
            self.assertIn(b'Review with AI',page.data)
            self.assertEqual(provider.call_count,0)
            self.assertNotIn(b'Review with AI',self.a.get(f'/workouts/{active}').data)
            self.assertEqual(self.a.get(f'/workouts/{current}/review').status_code,405)
            self.assertEqual(self.a.post(f'/workouts/{current}/review').status_code,400)
            self.assertEqual(self.post(self.b,f'/workouts/{current}/review').status_code,404)
            self.assertEqual(self.post(self.a,f'/workouts/{active}/review').status_code,409)
            anonymous=self.app.test_client()
            self.assertEqual(self.post(anonymous,f'/workouts/{current}/review').status_code,302)
            self.assertEqual(provider.call_count,0)
            response,context=self.review(current)
            self.assertEqual(response.status_code,200)
            self.assertEqual(context['review']['mode'],'ai')
            self.assertEqual(provider.call_count,1)
            self.assertEqual(response.headers['Cache-Control'],'no-store')

    def test_weighted_comparisons_targets_and_prs_are_server_calculated_and_read_only(self):
        old=self.session('2026-10-26T12:00:00+00:00')
        self.log(old,'40',7); self.log(old,'40',8,2)
        current=self.session(); self.log(current,'40',8); self.log(current,'40',9,2)
        with self.connect_db() as conn:
            before={table:conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() for table in ['workout_session','set_log','workout_plan','plan_exercise','body_measurement']}
        with patch('app.services.workout_review.query',wraps=workout_review.query) as reads:
            data=self.data(current)
        self.assertEqual(reads.call_count,1)
        self.assertEqual(data['targets'][0]['weight_kg'],'40.00')
        self.assertEqual((data['targets'][0]['sets'],data['targets'][0]['reps_min'],data['targets'][0]['reps_max']),(2,9,12))
        self.assertIn('(+1)',next(row['text'] for row in data['facts'] if row['kind']=='comparison'))
        self.assertEqual(data['exercises'][0]['previous_sets'][0]['session_id'],old)
        self.assertEqual(data['exercises'][0]['records'][0]['kind'],'load_reps')
        with patch('app.services.workout_ai.review',side_effect=self.choose):
            response,context=self.review(current)
        self.assertEqual(len(context['review']['suggestions']),1)
        self.assertIn('9–12 reps'.encode(),response.data)
        self.assertIn(b'Advisory only.',response.data)
        with self.connect_db() as conn:
            for table,values in before.items():self.assertEqual(conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall(),values)

    def test_unweighted_reps_are_separate_from_zero_load_and_previous_types(self):
        weighted=self.session('2026-10-25T12:00:00+00:00');self.log(weighted,'0',99)
        body=self.session('2026-10-26T12:00:00+00:00');self.log(body,None,6);self.log(body,None,7,2)
        # More recent weighted work must not mask the prior bodyweight session.
        latest=self.session('2026-10-27T12:00:00+00:00');self.log(latest,'10',5)
        current=self.session();self.log(current,None,8);self.log(current,None,9,2)
        data=self.data(current)
        self.assertIsNone(data['targets'][0]['weight_kg'])
        comparisons=[row['text'] for row in data['facts'] if row['kind']=='comparison']
        self.assertEqual(len(comparisons),1)
        self.assertIn('unweighted bodyweight',comparisons[0])
        self.assertIn('7 → 9 (+2)',comparisons[0])
        self.assertEqual(next(row['session_id'] for row in data['exercises'][0]['previous_sets'] if row['weight_kg'] is None),body)
        mixed=self.session('2026-11-03T12:00:00+00:00')
        self.log(mixed,None,10);self.log(mixed,'0',8,2)
        self.assertEqual(self.data(mixed)['targets'],[])

    def test_previous_sessions_exclude_active_future_current_and_other_accounts(self):
        with self.b.session_transaction() as state:other=state['user_id']
        older=self.session('2026-10-26T12:00:00+00:00');self.log(older,'40',5)
        tie=self.session();self.log(tie,'40',7)
        outsider=self.session(owner=other);self.log(outsider,'40',999)
        active=self.session(active=True);self.log(active,'40',888)
        future=self.session('2026-11-03T12:00:00+00:00');self.log(future,'40',777)
        current=self.session();self.log(current,'40',8);self.log(current,'40',9,2)
        captured=[]
        def model(data,schema):captured.append(data);return self.choose(data,schema)
        with patch('app.services.workout_ai.review',side_effect=model):
            response,_=self.review(current)
        self.assertEqual(response.status_code,200)
        self.assertEqual({row['session_id'] for row in captured[0]['exercises'][0]['previous_sets']},{tie})
        self.assertNotIn('999',json.dumps(captured))
        self.assertNotIn('888',json.dumps(captured))
        self.assertNotIn('777',json.dumps(captured))

    def test_missing_history_prescription_and_legacy_ids_do_not_invent_targets(self):
        no_history=self.session();self.log(no_history,'40',8);self.log(no_history,'40',9,2)
        self.assertEqual(self.data(no_history)['targets'],[])
        current=self.session('2026-11-03T12:00:00+00:00',notes='Original prescription unavailable')
        self.log(current,'40',10);self.log(current,'40',10,2)
        data=self.data(current)
        self.assertIsNone(data['workout']['prescription_snapshot'])
        self.assertTrue(any(row['kind']=='comparison' for row in data['facts']))
        self.assertEqual(data['targets'],[])
        legacy=self.session('2026-11-04T12:00:00+00:00',notes=self.snapshot(exercise_id=None))
        self.log(legacy,'40',10);self.log(legacy,'40',10,2)
        self.assertEqual(self.data(legacy)['targets'],[])
        empty=self.session(notes='No sets')
        self.assertEqual(len(self.data(empty)['facts']),1)

    def test_mixed_loads_inactive_timed_and_out_of_range_sets_have_no_targets(self):
        old=self.session('2026-10-26T12:00:00+00:00');self.log(old,'40',7)
        for index,(loads,reps) in enumerate([(['40','45'],[8,8]),(['40','40'],[13,13]),(['40','40'],[12,12]),(['40','40'],[0,8])]):
            current=self.session(f'2026-11-0{index+2}T12:00:00+00:00')
            for number,(weight,rep) in enumerate(zip(loads,reps),1):self.log(current,weight,rep,number)
            self.assertEqual(self.data(current)['targets'],[])
        current=self.session('2026-11-06T12:00:00+00:00');self.log(current,'40',8);self.log(current,'40',9,2)
        with self.connect_db() as conn:conn.execute('UPDATE exercise SET is_active=false WHERE exercise_id=%s',(self.exercise,))
        try:self.assertEqual(self.data(current)['targets'],[])
        finally:
            with self.connect_db() as conn:conn.execute('UPDATE exercise SET is_active=true WHERE exercise_id=%s',(self.exercise,))
        with self.connect_db() as conn:timed=conn.execute("SELECT exercise_id FROM exercise WHERE tracking_type='duration' LIMIT 1").fetchone()[0]
        legacy=self.session();self.log(legacy,None,45,exercise=timed)
        self.assertEqual(self.data(legacy)['targets'],[])
        self.assertTrue(any(row['kind']=='unsupported' for row in self.data(legacy)['facts']))

    def test_fabricated_numbers_exercises_text_targets_duplicates_and_extra_keys_fall_back(self):
        old=self.session('2026-10-26T12:00:00+00:00');self.log(old,'40',7)
        current=self.session();self.log(current,'40',8);self.log(current,'40',9,2)
        good=json.loads(self.choose(self.data(current),{}))
        invalid=[dict(highlights=['exercise_999'],suggestions=[]),dict(highlights=[999],suggestions=[]),
                 dict(good,weight_kg=999),dict(good,summary='Lift 999 kg'),
                 dict(highlights=['fact_1','fact_1'],suggestions=[]),
                 dict(good,suggestions=[dict(target_id='target_999',explanation_id='consistency')]),
                 dict(good,suggestions=[dict(target_id='target_1',explanation_id='Lift 999 kg')]),
                 dict(good,suggestions=[dict(target_id='target_1',explanation_id='consistency',reps=999)]),
                 dict(good,suggestions=good['suggestions']*2),dict(good,suggestions=good['suggestions']*4)]
        for value in invalid:
            with self.subTest(value=value),patch('app.services.workout_ai.review',return_value=json.dumps(value)):
                response,context=self.review(current)
                self.assertEqual(context['review']['mode'],'fallback')
                self.assertNotIn('999',json.dumps(context['review']))
                self.assertNotIn(b'Lift 999 kg',response.data)
                self.assertIn(b'AI review is unavailable',response.data)

    def test_provider_failure_timeout_malformed_json_and_oversize_response_fall_back(self):
        current=self.session();self.log(current,'40',8)
        for error in ['timed out','busy','key unavailable','provider rejected']:
            with self.subTest(error=error),patch('app.services.workout_ai.review',side_effect=workout_ai.InferenceError(error+' private-key')):
                response,context=self.review(current)
                self.assertEqual(context['review']['mode'],'fallback')
                self.assertNotIn(b'private-key',response.data)
                self.assertIn(b'1 logged set',response.data)
        for raw in ['not json','null','{"highlights":[],"suggestions":[]}','x'*40000]:
            with patch('app.services.workout_ai.review',return_value=raw):self.assertEqual(self.review(current)[1]['review']['mode'],'fallback')

    def test_current_edited_plan_is_never_used_instead_of_saved_snapshot(self):
        plan=self.create_plan();day=self.create_day(plan);self.create_entry(plan,day)
        old=self.session('2026-10-26T12:00:00+00:00');self.log(old,'40',7)
        current=self.session();self.log(current,'40',8);self.log(current,'40',9,2)
        with self.connect_db() as conn:conn.execute('UPDATE workout_session SET day_id=%s WHERE session_id=%s',(day,current))
        self.post(self.a,f'/plans/{plan}/days/{day}/exercises/new',exercise_id=self.exercise,target_sets=5,reps_min=20,reps_max=30)
        data=self.data(current)
        self.assertEqual(data['targets'][0]['sets'],2)
        self.post(self.a,f'/plans/{plan}/delete',confirm='delete')
        self.assertEqual(self.data(current)['targets'][0]['sets'],2)
