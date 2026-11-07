"""Owned date-only scheduling, completion zones and serialized start behavior."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import re
import time
from urllib.parse import urlsplit,parse_qs
from flask import template_rendered
from app.cli import upgrade
from app.routes.workouts import rest_serializer
from test_plans_postgres import PostgresTestCase


class WorkoutCalendarTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.plan=self.create_plan('Calendar strength')
        self.day=self.create_day(self.plan,'Upper calendar')
        self.entry=self.create_entry(self.plan,self.day)

    def token(self,path,client=None):
        response=(client or self.a).get(path)
        self.assertEqual(response.status_code,200)
        return re.search(r'name="preview" value="([^"]+)"',response.get_data(as_text=True)).group(1)

    def schedule(self,when='2026-11-07',day=None):
        token=self.token('/workouts/calendar/new')
        data=dict(day_id=day or self.day,scheduled_on=when,preview=token)
        response=self.post(self.a,'/workouts/calendar/new',**data)
        self.assertEqual(response.status_code,302)
        return int(response.location.rsplit('/',1)[1]),data

    def action(self,identifier,action,**changes):
        token=self.token(f'/workouts/calendar/{identifier}')
        return self.post(self.a,f'/workouts/calendar/{identifier}/{action}',preview=token,**changes)

    def context(self,path,client=None):
        contexts=[]
        def capture(sender,template,context,**extra):contexts.append(context)
        with template_rendered.connected_to(capture,self.app):response=(client or self.a).get(path)
        self.assertEqual(response.status_code,200)
        return contexts[-1]

    def test_multiple_per_date_and_repeat_create_no_sessions_or_duplicate_entries(self):
        identifier,data=self.schedule()
        response=self.post(self.a,'/workouts/calendar/new',**data)
        self.assertEqual(response.location,f'/workouts/calendar/{identifier}')
        self.schedule()
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM workout_schedule').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM workout_session').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT scheduled_on FROM workout_schedule LIMIT 1').fetchone()[0],date(2026,11,7))
        self.assertEqual(self.post(self.a,'/workouts/calendar/new',**{**data,'scheduled_on':'2026-11-08'}).status_code,409)

    def test_reschedule_stale_preview_cancel_and_past_not_completed(self):
        identifier,_=self.schedule('2020-01-01');path=f'/workouts/calendar/{identifier}'
        self.assertIn(b'Not completed',self.a.get(path).data)
        self.assertIn(b'Not completed',self.a.get('/workouts/calendar?month=2020-01').data)
        old=self.token(path)
        self.assertEqual(self.post(self.a,path+'/reschedule',preview=old,scheduled_on='2026-12-01').status_code,302)
        self.assertEqual(self.post(self.a,path+'/reschedule',preview=old,scheduled_on='2026-12-02').status_code,409)
        self.assertEqual(self.action(identifier,'cancel',confirm='cancel').status_code,302)
        self.assertEqual(self.post(self.a,path+'/cancel',preview=old,confirm='cancel').status_code,302)
        self.assertEqual(self.post(self.a,path+'/start',preview=old).status_code,409)
        self.assertIn(b'Cancelled',self.a.get(path).data)

    def test_ownership_csrf_invalid_dates_and_invalid_plan_day(self):
        identifier,data=self.schedule()
        for suffix in ('','/start','/cancel','/reschedule'):
            response=self.b.get(f'/workouts/calendar/{identifier}') if not suffix else self.post(self.b,f'/workouts/calendar/{identifier}'+suffix,preview=data['preview'])
            self.assertEqual(response.status_code,404)
        self.assertEqual(self.a.post(f'/workouts/calendar/{identifier}/start').status_code,400)
        for change in (dict(day_id='NaN'),dict(scheduled_on='2026-02-30'),dict(scheduled_on='2026-11-07T00:00:00Z')):
            self.assertEqual(self.post(self.a,'/workouts/calendar/new',**{**data,**change}).status_code,400)
        other=self.create_plan('Bob',client=self.b)
        with self.connect_db() as conn:
            day=conn.execute("INSERT INTO workout_day(plan_id,name,position) VALUES (%s,'Private Bob day',1) RETURNING day_id",(other,)).fetchone()[0]
        self.assertEqual(self.post(self.a,'/workouts/calendar/new',**{**data,'day_id':day,'preview':self.token('/workouts/calendar/new')}).status_code,404)
        self.assertNotIn(b'Upper calendar',self.b.get('/workouts/calendar?month=2026-11').data)

    def test_start_finish_once_repeated_start_and_manual_start_resume_same_session(self):
        identifier,_=self.schedule()
        token=self.token(f'/workouts/calendar/{identifier}')
        response=self.post(self.a,f'/workouts/calendar/{identifier}/start',preview=token)
        workout=int(response.location.rsplit('/',1)[1])
        self.assertEqual(self.post(self.a,f'/workouts/calendar/{identifier}/start',preview=token).location,response.location)
        self.assertEqual(self.post(self.a,f'/workouts/start/{self.plan}/{self.day}').location,response.location)
        self.post(self.a,f'/workouts/{workout}/finish');self.post(self.a,f'/workouts/{workout}/finish')
        self.assertEqual(self.post(self.a,f'/workouts/calendar/{identifier}/start',preview=token).location,response.location)
        self.assertEqual(self.post(self.a,f'/workouts/calendar/{identifier}/reschedule',preview=token,scheduled_on='2027-01-01').status_code,409)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT session_id FROM workout_schedule WHERE schedule_id=%s',(identifier,)).fetchone()[0],workout)
            self.assertEqual(conn.execute('SELECT count(*) FROM workout_session').fetchone()[0],1)
        self.assertIn(b'entry is fulfilled once',self.a.get(f'/workouts/calendar/{identifier}').data)

    def test_concurrent_start_creates_one_session_and_one_link(self):
        identifier,_=self.schedule();token=self.token(f'/workouts/calendar/{identifier}')
        clients=[self.app.test_client(),self.app.test_client()]
        for client in clients:client._cookies=copy.deepcopy(self.a._cookies)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda client:self.post(client,f'/workouts/calendar/{identifier}/start',preview=token),clients))
        self.assertTrue(all(r.status_code==302 for r in responses))
        self.assertEqual(responses[0].location,responses[1].location)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM workout_session').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT count(*) FROM workout_schedule WHERE session_id IS NOT NULL').fetchone()[0],1)

    def test_two_entries_never_claim_the_same_active_session(self):
        one,_=self.schedule();two,_=self.schedule()
        first=self.action(one,'start');second=self.action(two,'start')
        self.assertEqual(first.location,second.location)
        with self.connect_db() as conn:
            self.assertIsNone(conn.execute('SELECT session_id FROM workout_schedule WHERE schedule_id=%s',(two,)).fetchone()[0])
        workout=int(first.location.rsplit('/',1)[1]);self.post(self.a,f'/workouts/{workout}/finish')
        next_session=self.action(two,'start')
        self.assertNotEqual(next_session.location,first.location)

    def test_concurrent_manual_and_calendar_start_share_one_active_session(self):
        identifier,_=self.schedule();token=self.token(f'/workouts/calendar/{identifier}')
        clients=[self.app.test_client(),self.app.test_client()]
        for client in clients:client._cookies=copy.deepcopy(self.a._cookies)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.post,clients[0],f'/workouts/start/{self.plan}/{self.day}')
            second=pool.submit(self.post,clients[1],f'/workouts/calendar/{identifier}/start',preview=token)
            self.assertEqual(first.result().location,second.result().location)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM workout_session').fetchone()[0],1)
            self.assertIsNotNone(conn.execute('SELECT session_id FROM workout_schedule WHERE schedule_id=%s',(identifier,)).fetchone()[0])

    def test_plan_deletion_concurrent_with_start_returns_conflict_without_deadlock(self):
        identifier,_=self.schedule();token=self.token(f'/workouts/calendar/{identifier}')
        client=self.app.test_client();client._cookies=copy.deepcopy(self.a._cookies)
        with self.connect_db() as conn:
            conn.execute('DELETE FROM workout_plan WHERE plan_id=%s',(self.plan,))
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(self.post,client,f'/workouts/calendar/{identifier}/start',preview=token)
                time.sleep(.1)
                self.assertFalse(pending.done())
                conn.commit()
                self.assertEqual(pending.result(timeout=5).status_code,409)
            self.assertEqual(conn.execute('SELECT count(*) FROM workout_session').fetchone()[0],0)

    def test_deleted_plan_and_day_keep_schedule_and_completed_history(self):
        pending,_=self.schedule('2026-10-04');completed,_=self.schedule('2026-10-04')
        response=self.action(completed,'start');workout=int(response.location.rsplit('/',1)[1])
        self.post(self.a,f'/workouts/{workout}/finish')
        with self.connect_db() as conn:
            conn.execute("UPDATE workout_session SET started_at='2026-10-04T10:00:00Z',finished_at='2026-10-04T11:00:00Z' WHERE session_id=%s",(workout,))
        self.post(self.a,f'/plans/{self.plan}/delete',confirm='delete')
        page=self.a.get('/workouts/calendar?month=2026-10')
        self.assertIn(b'Upper calendar',page.data);self.assertIn(b'Completed',page.data)
        self.assertIn(b'plan day deleted',page.data)
        self.assertEqual(self.action(pending,'start').status_code,409)
        self.assertEqual(self.post(self.a,f'/workouts/calendar/{completed}/start').location,response.location)
        self.assertIn(b'Calendar strength',self.a.get(response.location).data)

    def test_edited_prescription_requires_fresh_preview_and_explicit_acceptance(self):
        identifier,_=self.schedule();old=self.token(f'/workouts/calendar/{identifier}')
        with self.connect_db() as conn:conn.execute('UPDATE plan_exercise SET rest_seconds=120,reps_max=15 WHERE plan_exercise_id=%s',(self.entry,))
        self.assertEqual(self.post(self.a,f'/workouts/calendar/{identifier}/start',preview=old,accept_changes='yes').status_code,409)
        self.assertEqual(self.action(identifier,'start').status_code,409)
        response=self.action(identifier,'start',accept_changes='yes')
        self.assertEqual(response.status_code,302)
        self.assertIn(b'120',self.a.get(response.location).data)

    def test_completion_month_uses_timezone_and_completed_entry_is_not_duplicated(self):
        identifier,_=self.schedule('2026-10-30');response=self.action(identifier,'start');workout=int(response.location.rsplit('/',1)[1])
        with self.connect_db() as conn:
            conn.execute("UPDATE workout_session SET started_at='2026-10-31T21:00:00Z',finished_at='2026-10-31T22:30:00Z' WHERE session_id=%s",(workout,))
        november=self.context('/workouts/calendar?month=2026-11')['calendar']
        self.assertEqual([e['date'] for e in november['events']],[date(2026,11,1)])
        self.assertEqual(self.context('/workouts/calendar?month=2026-10')['calendar']['events'],[])
        self.post(self.a,'/nutrition/settings',timezone='America/Los_Angeles',allergies='',unwanted_ingredients='',dietary_preferences='')
        october=self.context('/workouts/calendar?month=2026-10')['calendar']
        self.assertEqual([e['date'] for e in october['events']],[date(2026,10,31)])
        self.assertEqual(self.context('/workouts/calendar?month=2026-11')['calendar']['events'],[])

    def test_dashboard_upcoming_is_owned_bounded_and_excludes_cancelled_and_finished(self):
        for _ in range(5):self.schedule('2099-01-01')
        cancelled,_=self.schedule('2098-01-01');self.action(cancelled,'cancel',confirm='cancel')
        context=self.context('/dashboard')
        self.assertEqual(len(context['upcoming']),3)
        self.assertTrue(all(row['scheduled_on']==date(2099,1,1) for row in context['upcoming']))
        self.assertEqual(self.context('/dashboard',self.b)['upcoming'],[])

    def test_upgrade_from_previous_schema_repeat_execution_preserves_logs(self):
        workout=int(self.post(self.a,f'/workouts/start/{self.plan}/{self.day}').location.rsplit('/',1)[1])
        self.post(self.a,f'/workouts/{workout}/sets/new',exercise_id=self.exercise,set_number=1,reps=8)
        with self.connect_db() as conn:
            before=conn.execute('SELECT * FROM set_log').fetchall()
            conn.execute('DROP TABLE workout_schedule')
            conn.execute('ALTER TABLE plan_exercise DROP COLUMN rest_seconds')
            conn.execute("DELETE FROM schema_migration WHERE version='012_workout_calendar'")
            self.assertEqual(upgrade(conn),['012_workout_calendar']);self.assertEqual(upgrade(conn),[])
            self.assertEqual(conn.execute('SELECT * FROM set_log').fetchall(),before)

    def test_rest_event_only_after_insert_uses_snapshot_and_rejects_foreign_timer_access(self):
        with self.connect_db() as conn:conn.execute('UPDATE plan_exercise SET rest_seconds=120 WHERE plan_exercise_id=%s',(self.entry,))
        workout=int(self.post(self.a,f'/workouts/start/{self.plan}/{self.day}').location.rsplit('/',1)[1]);base=f'/workouts/{workout}'
        with self.connect_db() as conn:conn.execute('UPDATE plan_exercise SET rest_seconds=45 WHERE plan_exercise_id=%s',(self.entry,))
        response=self.post(self.a,base+'/sets/new',exercise_id=self.exercise,set_number=1,reps=8)
        token=parse_qs(urlsplit(response.location).query)['rest'][0]
        with self.app.app_context():event=rest_serializer().loads(token)
        self.assertEqual(event['seconds'],120)
        self.assertIsNotNone(self.context(response.location)['timer']['event'])
        self.assertEqual(self.post(self.a,base+'/sets/new',exercise_id=self.exercise,set_number=1,reps=8).status_code,200)
        self.assertIsNone(self.context(base+'/sets/new')['timer']['event'])
        self.assertEqual(self.b.get(base+'/timer-state').status_code,404)
        self.assertEqual(self.a.get(base+'/timer-state').json,dict(active=True))
        self.post(self.a,base+'/finish')
        self.assertEqual(self.a.get(base+'/timer-state').json,dict(active=False))
        self.assertTrue(self.context(base)['timer']['finished'])
        self.assertEqual(self.post(self.a,base+'/sets/new',exercise_id=self.exercise,set_number=2,reps=8).status_code,409)

    def test_missing_rest_defaults_to_manual_and_edits_do_not_start_timer(self):
        workout=int(self.post(self.a,f'/workouts/start/{self.plan}/{self.day}').location.rsplit('/',1)[1]);base=f'/workouts/{workout}'
        response=self.post(self.a,base+'/sets/new',exercise_id=self.exercise,set_number=1,reps=8)
        with self.app.app_context():event=rest_serializer().loads(parse_qs(urlsplit(response.location).query)['rest'][0])
        self.assertIsNone(event['seconds'])
        with self.connect_db() as conn:set_id=conn.execute('SELECT set_id FROM set_log WHERE session_id=%s',(workout,)).fetchone()[0]
        edited=self.post(self.a,base+f'/sets/{set_id}/edit',exercise_id=self.exercise,set_number=1,reps=9)
        self.assertEqual(edited.location,base)
