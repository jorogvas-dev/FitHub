import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.cli import upgrade
from app.services.workout_ai import InferenceError
from test_plans_postgres import PostgresTestCase


class WorkoutDraftTests(PostgresTestCase):
    def setup_preferences(self, client=None, **overrides):
        client = client or self.a
        response = self.post(client,'/onboarding',height_cm='175.5',weight_kg='80.25',goal='strength',experience='beginner')
        self.assertEqual(response.status_code,302)
        response = self.post(client,'/onboarding/training',days_per_week=2,minutes_per_session=30,
                             equipment=[],avoid_movements=[],**overrides)
        self.assertEqual(response.status_code,302)
        with self.connect_db() as conn:
            self.exercise = conn.execute("SELECT exercise_id FROM exercise WHERE name='Bodyweight squat'").fetchone()[0]

    def response_document(self):
        return dict(name='Local draft',days=[dict(name=name,estimated_minutes=15,
            exercises=[dict(exercise_id=self.exercise,sets=3,reps_min=8,reps_max=12)]) for name in ['Day A','Day B']])

    def generate(self, document=None, client=None):
        with patch('app.routes.workout_ai.inference.generate', return_value=json.dumps(document or self.response_document())) as model:
            response = self.post(client or self.a,'/plans/generate')
        return response, model

    def draft_count(self):
        with self.connect_db() as conn:
            return tuple(conn.execute('SELECT (SELECT count(*) FROM workout_draft),(SELECT count(*) FROM workout_plan),'
                                      '(SELECT count(*) FROM workout_session),(SELECT count(*) FROM set_log)').fetchone())

    def edit_fields(self, path, **overrides):
        with self.connect_db() as conn:
            draft = conn.execute('SELECT document,revision FROM workout_draft WHERE draft_id=%s',(path.rsplit('/',1)[1],)).fetchone()
        fields = dict(name=draft[0]['name'],revision=draft[1],action='update')
        for i,day in enumerate(draft[0]['days']):
            fields[f'd{i}_name']=day['name']
            for j,row in enumerate(day['exercises']):
                fields.update({f'd{i}_e{j}_'+key:str(value) for key,value in row.items()})
        return {**fields,**overrides}

    def test_optional_step_skip_edit_and_profile_preservation(self):
        self.post(self.a,'/onboarding',height_cm=180,weight_kg=78,goal='strength',experience='advanced')
        with self.connect_db() as conn:
            before=(conn.execute('SELECT * FROM fitness_profile WHERE user_id=%s',(self.owner,)).fetchall(),
                    conn.execute('SELECT * FROM body_measurement WHERE user_id=%s',(self.owner,)).fetchall())
        self.assertEqual(self.post(self.a,'/onboarding/training',action='skip').location,'/dashboard')
        self.assertEqual(self.a.get('/plans/new').status_code,200)
        self.assertIn(b'Complete your optional training preferences',self.a.get('/plans/generate').data)
        self.assertEqual(self.post(self.a,'/onboarding/training',days_per_week=3,minutes_per_session=45,
                                   equipment=['dumbbells'],avoid_movements=['squat'],user_id=999).status_code,302)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT * FROM fitness_profile WHERE user_id=%s',(self.owner,)).fetchall(),before[0])
            self.assertEqual(conn.execute('SELECT * FROM body_measurement WHERE user_id=%s',(self.owner,)).fetchall(),before[1])
            self.assertEqual(conn.execute('SELECT days_per_week,equipment,status FROM training_preferences WHERE user_id=%s',(self.owner,)).fetchone(),(3,['dumbbells'],'completed'))
        self.post(self.a,'/onboarding/training',action='skip')
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT equipment FROM training_preferences WHERE user_id=%s',(self.owner,)).fetchone()[0],['dumbbells'])
        self.assertEqual(self.draft_count(),(0,0,0,0))

    def test_invalid_preferences_leave_existing_profile_and_preferences_unchanged(self):
        for values in [dict(days_per_week=0),dict(minutes_per_session=181),dict(equipment=['unknown']),dict(avoid_movements=['unknown'])]:
            data=dict(days_per_week=2,minutes_per_session=30,equipment=[],avoid_movements=[]);data.update(values)
            self.assertEqual(self.post(self.a,'/onboarding/training',**data).status_code,200)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM training_preferences').fetchone()[0],0)

    def test_generation_preview_edit_and_only_confirmed_save_creates_plan(self):
        self.setup_preferences()
        self.assertIn(b'Generate workout plan',self.a.get('/plans').data)
        self.assertEqual(self.a.get('/plans/generate').status_code,200)
        self.assertEqual(self.draft_count(),(0,0,0,0))
        response,model=self.generate();self.assertEqual(response.status_code,302)
        path=response.location
        self.assertEqual(self.draft_count(),(1,0,0,0))
        self.assertLessEqual(len(model.call_args.args[2]),48)
        self.assertEqual(model.call_args.args[1]['height_cm'],'175.5')
        self.assertEqual(model.call_args.args[1]['weight_kg'],'80.25')
        self.assertIn(b'Estimated 13 minutes',self.a.get(path).data)
        self.assertIn(b'Local draft',self.a.get('/plans').data)
        self.assertEqual(self.b.get(path).status_code,404)
        self.assertEqual(self.post(self.b,path,**self.edit_fields(path,action='save',confirm='save')).status_code,404)
        self.assertEqual(self.post(self.a,path,**self.edit_fields(path,name='Edited draft',d0_e0_sets='4')).status_code,302)
        self.assertEqual(self.draft_count(),(1,0,0,0))
        fields=self.edit_fields(path,action='save')
        self.assertIn(b'Confirm Save as new plan',self.post(self.a,path,**fields).data)
        self.assertEqual(self.draft_count(),(1,0,0,0))
        fields['confirm']='save'
        saved=self.post(self.a,path,**fields)
        self.assertEqual(saved.status_code,302)
        self.assertEqual(self.draft_count(),(1,1,0,0))
        self.assertIn(b'Edited draft',self.a.get(saved.location).data)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM workout_day').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT target_sets FROM plan_exercise ORDER BY position,day_id').fetchone()[0],4)
        self.assertEqual(self.post(self.a,path,**fields).location,saved.location)
        self.assertEqual(self.draft_count(),(1,1,0,0))

    def test_invalid_response_timeout_and_missing_profile_do_not_create_drafts(self):
        with patch('app.routes.workout_ai.inference.generate') as model:
            self.assertIn(b'Complete your optional',self.post(self.a,'/plans/generate').data)
            model.assert_not_called()
        self.setup_preferences()
        for field,value in [('exercise_id',999999),('sets',0),('reps_min',True),('seconds',30)]:
            doc=self.response_document();doc['days'][0]['exercises'][0][field]=value
            response,_=self.generate(doc);self.assertEqual(response.status_code,200)
            self.assertIn(b'role="alert"',response.data)
        for raw in ['not json','{"name":"x","name":"y"}','{"days":NaN}']:
            with patch('app.routes.workout_ai.inference.generate',return_value=raw):
                self.assertEqual(self.post(self.a,'/plans/generate').status_code,200)
        with patch('app.routes.workout_ai.inference.generate',side_effect=InferenceError('Local AI timed out')):
            page=self.post(self.a,'/plans/generate')
            self.assertIn(b'timed out',page.data)
            self.assertIn(b'Use manual plan builder',page.data)
        self.assertEqual(self.draft_count(),(0,0,0,0))

    def test_model_duplicate_is_corrected_but_edit_and_save_still_reject_duplicates(self):
        self.setup_preferences()
        valid=self.response_document()
        duplicate=self.response_document()
        duplicate['days'][0]['exercises'].append(dict(exercise_id=self.exercise,sets=2,reps_min=6,reps_max=8))
        with patch('app.services.workout_ai._api_key',return_value='test-key-not-real'), \
                patch('app.services.workout_ai._request',side_effect=[json.dumps(duplicate),json.dumps(valid)]) as send:
            response=self.post(self.a,'/plans/generate')
        self.assertEqual(response.status_code,302)
        self.assertEqual(send.call_count,2)
        self.assertEqual(self.draft_count(),(1,0,0,0))
        path=response.location
        self.assertIn(b'Save as new plan',self.a.get(path).data)
        fields=self.edit_fields(path,d0_e1_exercise_id=str(self.exercise),d0_e1_sets='2',d0_e1_reps_min='6',d0_e1_reps_max='8')
        for action in ('update','save'):
            page=self.post(self.a,path,**{**fields,'action':action,'confirm':'save'})
            self.assertEqual(page.status_code,200)
            self.assertIn(b'An exercise may only appear once',page.data)
            self.assertEqual(self.draft_count(),(1,0,0,0))
        with self.connect_db() as conn:
            document=conn.execute('SELECT document FROM workout_draft WHERE draft_id=%s',(path.rsplit('/',1)[1],)).fetchone()[0]
        self.assertEqual(len(document['days'][0]['exercises']),1)

    def test_failed_correction_does_not_store_any_invalid_draft_or_plan(self):
        self.setup_preferences()
        duplicate=self.response_document()
        duplicate['days'][0]['exercises'] *= 2
        with patch('app.services.workout_ai._api_key',return_value='test-key-not-real'), \
                patch('app.services.workout_ai._request',return_value=json.dumps(duplicate)) as send:
            response=self.post(self.a,'/plans/generate')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'The AI could not produce a valid workout draft',response.data)
        self.assertIn(b'Use manual plan builder',response.data)
        self.assertEqual(send.call_count,2)
        self.assertEqual(self.draft_count(),(0,0,0,0))

    def test_save_revalidates_admin_changes_preferences_and_forged_fields(self):
        self.setup_preferences()
        path=self.generate()[0].location
        fields=self.edit_fields(path,action='save',confirm='save')
        with self.connect_db() as conn:
            conn.execute('UPDATE exercise SET is_active=false WHERE exercise_id=%s',(self.exercise,))
        self.assertIn(b'An exercise is unavailable',self.post(self.a,path,**fields).data)
        with self.connect_db() as conn:
            conn.execute('UPDATE exercise SET is_active=true WHERE exercise_id=%s',(self.exercise,))
        self.post(self.a,'/onboarding/training',days_per_week=2,minutes_per_session=30,avoid_movements=['squat'])
        self.assertIn(b'An exercise is unavailable',self.post(self.a,path,**fields).data)
        self.assertEqual(self.post(self.a,path,**{**fields,'revision':'invalid'}).status_code,400)
        self.assertEqual(self.post(self.a,path,**{**fields,'revision':'99'}).status_code,409)
        self.assertEqual(self.a.post(path,data=fields).status_code,400)
        self.assertEqual(self.draft_count(),(1,0,0,0))

    def test_preferences_and_catalogue_changes_during_inference_are_rechecked(self):
        self.setup_preferences()
        def changed(*args):
            with self.connect_db() as conn:
                conn.execute("UPDATE training_preferences SET avoid_movements=ARRAY['squat'] WHERE user_id=%s",(self.owner,))
            return json.dumps(self.response_document())
        with patch('app.routes.workout_ai.inference.generate',side_effect=changed):
            self.assertIn(b'preferences changed',self.post(self.a,'/plans/generate').data)
        self.assertEqual(self.draft_count(),(0,0,0,0))

    def test_concurrent_confirmations_save_one_plan_without_workout_logs(self):
        self.setup_preferences()
        path=self.generate()[0].location
        fields=self.edit_fields(path,action='save',confirm='save')
        clients=[self.app.test_client(),self.app.test_client()]
        for client in clients:
            with client.session_transaction() as state:
                state['user_id']=self.owner
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda client:self.post(client,path,**fields),clients))
        self.assertEqual([response.status_code for response in responses],[302,302])
        self.assertEqual(responses[0].location,responses[1].location)
        self.assertEqual(self.draft_count(),(1,1,0,0))

    def test_additive_migration_is_repeatable_and_does_not_modify_existing_records(self):
        self.setup_preferences()
        with self.connect_db() as conn:
            before=conn.execute('SELECT * FROM fitness_profile').fetchall(),conn.execute('SELECT * FROM body_measurement').fetchall()
            conn.execute('DROP TABLE workout_draft,training_preferences')
            conn.execute("DELETE FROM schema_migration WHERE version='007_workout_drafts'")
            self.assertEqual(upgrade(conn),['007_workout_drafts'])
            self.assertEqual(upgrade(conn),[])
            self.assertEqual(conn.execute('SELECT * FROM fitness_profile').fetchall(),before[0])
            self.assertEqual(conn.execute('SELECT * FROM body_measurement').fetchall(),before[1])
