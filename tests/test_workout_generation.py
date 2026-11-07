import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx

from app import create_app
from app.services import workout_ai
from app.services.workout_generation import DuplicateExerciseError, candidates, strict_json, validate_document


class WorkoutGenerationTests(unittest.TestCase):
    def setUp(self):
        credential = patch('app.services.workout_ai._api_key', return_value='test-token-not-real')
        credential.start()
        self.addCleanup(credential.stop)
        self.preferences = dict(days_per_week=1, minutes_per_session=30, equipment=[], avoid_movements=[])
        self.catalogue = [dict(exercise_id=1, name='Bodyweight squat', equipment='Bodyweight',
                               muscle_group='Legs', is_active=True, tracking_type='reps'),
                          dict(exercise_id=2, name='Plank', equipment='Bodyweight',
                               muscle_group='Core', is_active=True, tracking_type='duration'),
                          dict(exercise_id=3, name='Bench press', equipment='Barbell',
                               muscle_group='Chest', is_active=True, tracking_type='reps')]
        self.document = dict(name='Draft', days=[dict(name='Day 1', estimated_minutes=10,
            exercises=[dict(exercise_id=1, sets=3, reps_min=8, reps_max=12)])])

    def validate(self, document=None, preferences=None):
        return validate_document(document or self.document, preferences or self.preferences, self.catalogue, [1,2,3])

    def test_valid_draft_recomputes_duration_and_keeps_input_unchanged(self):
        original = copy.deepcopy(self.document)
        self.assertEqual(self.validate()['days'][0]['estimated_minutes'], 13)
        self.assertEqual(self.document, original)

    def test_candidate_equipment_exclusions_inactive_and_timed(self):
        self.assertEqual([row['exercise_id'] for row in candidates(self.catalogue,self.preferences)], [1])
        self.assertEqual(candidates(self.catalogue, {**self.preferences,'avoid_movements':['squat']}), [])
        self.assertEqual([row['exercise_id'] for row in candidates(self.catalogue,
            {**self.preferences,'equipment':['barbell']})], [1])  # Bench is also required.
        self.assertEqual(len(candidates(self.catalogue, {**self.preferences,'equipment':['barbell','bench']})), 2)
        self.catalogue[0]['is_active'] = False
        self.assertEqual(candidates(self.catalogue,self.preferences), [])
        machine=dict(exercise_id=4,name='Machine shoulder press',equipment='Shoulder press machine',
                     muscle_group='Shoulders',is_active=True,tracking_type='reps')
        self.assertEqual(candidates([machine],{**self.preferences,'equipment':['machines']}),[machine])
        self.assertEqual(candidates([machine],{**self.preferences,'equipment':['machines'],'avoid_movements':['overhead']}),[])

    def test_invalid_structures_prescriptions_ids_and_duration_are_rejected(self):
        changes = [dict(exercise_id=999),dict(exercise_id=2),dict(exercise_id=3),dict(sets=True),
                   dict(sets=0),dict(sets=7),dict(reps_min=0),dict(reps_max=31),
                   dict(reps_min=13,reps_max=12),dict(seconds=30),dict(sets=3.0)]
        for change in changes:
            doc = copy.deepcopy(self.document)
            doc['days'][0]['exercises'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(doc)
        for field,value in [('days',[]),('name',''),('extra','field')]:
            doc = copy.deepcopy(self.document);doc[field]=value
            with self.assertRaises(ValueError):self.validate(doc)
        doc = copy.deepcopy(self.document)
        doc['days'][0]['exercises'] *= 2
        with self.assertRaises(ValueError):self.validate(doc)
        doc = copy.deepcopy(self.document)
        doc['days'][0]['exercises'][0]['sets'] = 6
        with self.assertRaises(ValueError):self.validate(doc,{**self.preferences,'minutes_per_session':15})
        with self.assertRaises(ValueError):self.validate(preferences={**self.preferences,'avoid_movements':['squat']})

    def test_strict_json_rejects_duplicate_keys_prose_nonfinite_and_oversize(self):
        for raw in ['{"name":"a","name":"b"}', '{"x":NaN}', '```json\n{}\n```', 'x'*40000, 'null']:
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                validate_document(strict_json(raw), self.preferences, self.catalogue, [1,2,3])

    def test_generation_corrects_duplicate_exercise_ids_before_returning_a_draft(self):
        duplicate=copy.deepcopy(self.document)
        # Different prescriptions with the same ID must also be corrected.
        duplicate['days'][0]['exercises'].append(dict(exercise_id=1,sets=2,reps_min=6,reps_max=8))
        app=create_app({'TESTING':True,'SECRET_KEY':'test-only'})
        with app.app_context(), patch('app.services.workout_ai._request',side_effect=[
                json.dumps(duplicate),json.dumps(self.document)]) as send:
            raw=workout_ai.generate(self.preferences,{},self.catalogue[:1])
        self.assertEqual(json.loads(raw),self.document)
        self.assertEqual(send.call_count,2)
        correction=send.call_args.args[1]
        self.assertIn('exercise_id 1',correction['messages'][-1]['content'])
        self.assertIn('training day 1',correction['messages'][-1]['content'])
        self.assertEqual(len(send.call_args_list[0].args[1]['messages']),2)
        self.assertEqual(len(correction['messages']),4)
        self.assertEqual(json.loads(correction['messages'][-2]['content']),duplicate)

    def test_repeated_invalid_correction_is_rejected_after_two_attempts(self):
        duplicate=copy.deepcopy(self.document)
        duplicate['days'][0]['exercises'] *= 2
        app=create_app({'TESTING':True,'SECRET_KEY':'test-only'})
        with app.app_context(), patch('app.services.workout_ai._request',return_value=json.dumps(duplicate)) as send:
            with self.assertRaisesRegex(workout_ai.InferenceError,'could not produce a valid workout draft'):
                workout_ai.generate(self.preferences,{},self.catalogue[:1])
        self.assertEqual(send.call_count,2)

    def test_correction_is_still_rejected_for_unsafe_exercises_and_prescriptions(self):
        duplicate=copy.deepcopy(self.document)
        duplicate['days'][0]['exercises'] *= 2
        app=create_app({'TESTING':True,'SECRET_KEY':'test-only'})
        for change in [dict(exercise_id=999),dict(exercise_id=2),dict(exercise_id=3),dict(sets=0),
                       dict(sets=7),dict(reps_min=13,reps_max=12),dict(seconds=30)]:
            invalid=copy.deepcopy(self.document)
            invalid['days'][0]['exercises'][0].update(change)
            with self.subTest(change=change), app.app_context(), patch('app.services.workout_ai._request',
                    side_effect=[json.dumps(duplicate),json.dumps(invalid)]) as send:
                with self.assertRaisesRegex(workout_ai.InferenceError,'could not produce a valid workout draft'):
                    workout_ai.generate(self.preferences,{},self.catalogue)
                self.assertEqual(send.call_count,2)

    def test_same_exercise_on_different_days_is_valid_without_correction(self):
        document=copy.deepcopy(self.document)
        document['days'].append({**copy.deepcopy(document['days'][0]),'name':'Day 2'})
        preferences={**self.preferences,'days_per_week':2}
        self.assertEqual(len(validate_document(document,preferences,self.catalogue,[1])['days']),2)
        app=create_app({'TESTING':True,'SECRET_KEY':'test-only'})
        with app.app_context(), patch('app.services.workout_ai._request',return_value=json.dumps(document)) as send:
            self.assertEqual(json.loads(workout_ai.generate(preferences,{},self.catalogue[:1])),document)
        self.assertEqual(send.call_count,1)

    def test_correction_shares_the_original_deadline_and_does_not_extend_it(self):
        duplicate=copy.deepcopy(self.document)
        duplicate['days'][0]['exercises'] *= 2
        app=create_app({'TESTING':True,'SECRET_KEY':'test-only','WORKOUT_AI_TIMEOUT_SECONDS':120})
        with app.app_context(), patch('app.services.workout_ai.monotonic',side_effect=[100,140]), \
                patch('app.services.workout_ai._request',side_effect=[json.dumps(duplicate),json.dumps(self.document)]) as send:
            workout_ai.generate(self.preferences,{},self.catalogue[:1])
        self.assertEqual(send.call_args_list[0].args[2],120)
        self.assertEqual(send.call_args_list[1].args[2],80)
        with app.app_context(), patch('app.services.workout_ai.monotonic',side_effect=[100,220]), \
                patch('app.services.workout_ai._request',return_value=json.dumps(duplicate)) as send:
            with self.assertRaisesRegex(workout_ai.InferenceError,'timed out'):
                workout_ai.generate(self.preferences,{},self.catalogue[:1])
        self.assertEqual(send.call_count,1)

    def test_transport_failures_and_oversized_content_do_not_trigger_correction(self):
        app=create_app({'TESTING':True,'SECRET_KEY':'test-only'})
        for error in ('API key rejected','timed out','unavailable'):
            with self.subTest(error=error), app.app_context(), patch('app.services.workout_ai._request',
                    side_effect=workout_ai.InferenceError(error)) as send:
                with self.assertRaisesRegex(workout_ai.InferenceError,error):
                    workout_ai.generate(self.preferences,{},self.catalogue[:1])
                self.assertEqual(send.call_count,1)
        with app.app_context(), patch('app.services.workout_ai._request',return_value='x'*32769) as send:
            with self.assertRaises(workout_ai.InferenceError):
                workout_ai.generate(self.preferences,{},self.catalogue[:1])
            self.assertEqual(send.call_count,1)

    def test_duplicate_error_identifies_the_day_and_id_without_rejecting_cross_day_reuse(self):
        document=copy.deepcopy(self.document)
        document['days'].append({**copy.deepcopy(document['days'][0]),'name':'Day 2'})
        document['days'][1]['exercises'].append(dict(exercise_id=1,sets=2,reps_min=6,reps_max=8))
        with self.assertRaises(DuplicateExerciseError) as caught:
            validate_document(document,{**self.preferences,'days_per_week':2},self.catalogue,[1])
        self.assertEqual((caught.exception.day_number,caught.exception.exercise_id),(2,1))

    def test_http_adapter_rejects_provider_errors_and_timeout_without_live_inference(self):
        app = create_app({'TESTING':True,'SECRET_KEY':'test-only'})
        real_client = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200,json={
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(self.document)}}]}))
        with app.app_context(), patch('app.services.workout_ai.httpx.Client',
                side_effect=lambda **kwargs:real_client(transport=transport,**kwargs)):
            self.assertEqual(json.loads(workout_ai.generate(self.preferences,{},self.catalogue[:1])),self.document)
        for status, body in [(503,{}),(200,{'choices':[]}),
                             (200,{'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}),
                             (200,{'choices':[{'message':{'content':''}}]})]:
            transport = httpx.MockTransport(lambda request: httpx.Response(status,json=body))
            with app.app_context(), patch('app.services.workout_ai.httpx.Client',
                    side_effect=lambda **kwargs:real_client(transport=transport,**kwargs)):
                with self.assertRaises(workout_ai.InferenceError):
                    workout_ai.generate(self.preferences,{},self.catalogue[:1])
        def timed_out(request):
            raise httpx.ReadTimeout('mock timeout')
        with app.app_context(), patch('app.services.workout_ai.httpx.Client',
                side_effect=lambda **kwargs:real_client(transport=httpx.MockTransport(timed_out),**kwargs)):
            with self.assertRaisesRegex(workout_ai.InferenceError,'timed out'):
                workout_ai.generate(self.preferences,{},self.catalogue[:1])

    def test_adapter_preserves_existing_endpoint_model_and_structured_schema(self):
        app = create_app({'TESTING':True,'SECRET_KEY':'test-only'})
        with app.app_context(), patch('app.services.workout_ai._request',return_value=json.dumps(self.document)) as send:
            raw = workout_ai.generate(self.preferences, dict(goal='strength'), self.catalogue[:1])
        self.assertEqual(json.loads(raw), self.document)
        endpoint, payload, timeout, token = send.call_args.args
        self.assertEqual(endpoint,workout_ai.DEFAULT_ENDPOINT)
        self.assertEqual(payload['model'],workout_ai.DEFAULT_MODEL)
        self.assertEqual(payload['response_format']['type'],'json_schema')
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['tool_ids'],[])
        self.assertEqual(payload['tools'],[])
        self.assertEqual(token,'test-token-not-real')
        self.assertEqual(timeout,workout_ai.DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(json.loads(payload['messages'][1]['content'])['eligible_exercises'][0]['exercise_id'],1)
        self.assertEqual(json.loads(payload['messages'][1]['content'])['max_total_sets_per_day'],10)
        day_schema=payload['response_format']['json_schema']['schema']['properties']['days']['items']
        self.assertEqual(day_schema['properties']['exercises']['maxItems'],1)

    def test_adapter_timeout_and_busy_do_not_wait_indefinitely(self):
        app = create_app({'TESTING':True,'SECRET_KEY':'test-only'})
        with app.app_context(), patch('app.services.workout_ai._workers.submit') as submit:
            submit.return_value.result.side_effect = workout_ai.FutureTimeout()
            # A mocked executor does not run work()/release the acquired slot.
            try:
                with self.assertRaisesRegex(workout_ai.InferenceError,'timed out'):
                    workout_ai.generate(self.preferences,{},self.catalogue[:1])
            finally:
                workout_ai._slots.release()
        with app.app_context(), patch('app.services.workout_ai._slots') as slots:
            slots.acquire.return_value=False
            with self.assertRaisesRegex(workout_ai.InferenceError,'busy'):
                workout_ai.generate(self.preferences,{},self.catalogue[:1])

    def test_configurable_model_and_tool_ids_use_actual_legacy_execution_path(self):
        app = create_app({'TESTING':True, 'SECRET_KEY':'test-only',
                          'WORKOUT_AI_ENDPOINT':'http://webui:8080/api/v1/chat/completions',
                          'WORKOUT_AI_MODEL':'configured-model',
                          'WORKOUT_AI_TOOL_IDS':'validation_tool, validation_tool,other_tool'})
        with app.app_context(), patch('app.services.workout_ai._request',return_value=json.dumps(self.document)) as send:
            workout_ai.generate(self.preferences, {}, self.catalogue[:1])
        endpoint, payload, _, _ = send.call_args.args
        self.assertEqual(endpoint,'http://webui:8080/api/v1/chat/completions')
        self.assertEqual(payload['model'],'configured-model')
        self.assertEqual(payload['tool_ids'],['validation_tool','other_tool'])
        self.assertEqual(payload['params'],{'function_calling':'legacy'})
        self.assertNotIn('tools',payload)

    def test_invalid_backend_config_rejected_before_network(self):
        for config in [dict(WORKOUT_AI_TOOL_IDS='tool/invalid'),dict(WORKOUT_AI_TOOL_IDS=['tool']),
                       dict(WORKOUT_AI_TOOL_IDS='a,b,c,d,e'),dict(WORKOUT_AI_TIMEOUT_SECONDS=181),
                       dict(WORKOUT_AI_TIMEOUT_SECONDS=0),dict(WORKOUT_AI_TIMEOUT_SECONDS=float('nan')),
                       dict(WORKOUT_AI_ENDPOINT='http://webui:wrong/api/chat/completions'),
                       dict(WORKOUT_AI_ENDPOINT='http://webui/api/chat/completions?key=secret'),
                       dict(WORKOUT_AI_ENDPOINT='http://user:secret@webui/api/chat/completions')]:
            app = create_app({'TESTING':True,'SECRET_KEY':'test-only',**config})
            with self.subTest(config=config), app.app_context(), patch('app.services.workout_ai._request') as send:
                with self.assertRaisesRegex(workout_ai.InferenceError,'configuration is invalid'):
                    workout_ai.generate(self.preferences,{},self.catalogue[:1])
                send.assert_not_called()

    def test_full_plan_wait_budget_accepts_slow_valid_completion_and_remains_bounded(self):
        # The real 3-day/48-exercise-candidate request took 36 seconds. The old
        # 20-second default/25-second cap could never return that valid response.
        for budget in (120,180):
            app = create_app({'TESTING':True,'SECRET_KEY':'test-only', 'WORKOUT_AI_TIMEOUT_SECONDS':budget})
            with app.app_context(), patch('app.services.workout_ai._workers.submit') as submit:
                submit.return_value.result.return_value=json.dumps(self.document)
                try:
                    result=workout_ai.generate(self.preferences,{},self.catalogue[:1])
                    self.assertEqual(json.loads(result),self.document)
                    submit.return_value.result.assert_called_once_with(timeout=budget)
                finally:
                    workout_ai._slots.release()


class OpenWebUITransportTests(unittest.TestCase):
    def request(self, body, status=200, tool_ids=None, inspect=None):
        def handler(request):
            if inspect:
                inspect(request)
            return httpx.Response(status, json=body)
        real_client = httpx.Client
        with patch('app.services.workout_ai.httpx.Client',side_effect=lambda **kwargs:
                   real_client(transport=httpx.MockTransport(handler),**kwargs)):
            return workout_ai._request(workout_ai.DEFAULT_ENDPOINT, {'tool_ids':tool_ids or []}, 2, 'fake-key-for-tests')

    def envelope(self, **extra):
        return {'choices':[{'finish_reason':'stop','message':{'content':'{"name":"synthetic"}'}}],**extra}

    def test_bearer_auth_sent_only_as_header(self):
        def inspect(request):
            self.assertEqual(request.headers['Authorization'],'Bearer fake-key-for-tests')
            self.assertNotIn('fake-key-for-tests',str(request.url))
            self.assertNotIn(b'fake-key-for-tests',request.content)
        self.assertEqual(json.loads(self.request(self.envelope(),inspect=inspect)),{'name':'synthetic'})

    def test_authentication_errors_are_clear_and_do_not_expose_key_or_response(self):
        for status in (401,403):
            with self.subTest(status=status), self.assertRaises(workout_ai.InferenceError) as caught:
                self.request({'detail':'private server detail fake-key-for-tests'},status=status)
            self.assertIn('rejected the API key',str(caught.exception))
            self.assertNotIn('fake-key-for-tests',str(caught.exception))
            self.assertNotIn('private server detail',str(caught.exception))

    def test_actual_tool_result_required_instead_of_model_narration_or_native_calls(self):
        for sources in ([], None, {}, [None], [{'tool_result':True,'source':None}],
                        [{'tool_result':True,'source':{'name':17}}],
                        [{'tool_result':False,'source':{'name':'validator/check'},'document':['{}']}],
                        [{'tool_result':True,'source':{'name':'other/check'},'document':['{}']}],
                        [{'tool_result':True,'source':{'name':'validator/check'},'document':[]}],
                        [{'tool_result':True,'source':{'name':'validator/check'},'document':'{}'}]):
            with self.subTest(sources=sources), self.assertRaisesRegex(workout_ai.InferenceError,'execution result'):
                self.request(self.envelope(sources=sources),tool_ids=['validator'])
        body=self.envelope(sources=[{'tool_result':True,'source':{'name':'validator/check'},'document':['{"valid":true}']}])
        self.assertEqual(json.loads(self.request(body,tool_ids=['validator'])),{'name':'synthetic'})
        body['choices'][0]['message']['tool_calls']=[{'function':{'name':'validator/check'}}]
        with self.assertRaisesRegex(workout_ai.InferenceError,'invalid response'):
            self.request(body,tool_ids=['validator'])

    def test_malformed_envelopes_fail_cleanly(self):
        for body in ([],None,{'choices':None},{'choices':[None]},{'choices':[{'message':None}]},
                     {'choices':[{'message':{'content':[]}}]}):
            with self.subTest(body=body), self.assertRaisesRegex(workout_ai.InferenceError,'invalid response'):
                self.request(body)

    def test_credential_file_is_bounded_and_missing_or_invalid_key_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic-key.txt'
            for value in (None,'','x'*4097,'fake\nheader','non-ascii-\u00e9'):
                if value is not None:
                    path.write_text(value,encoding='utf-8')
                with self.subTest(value=value and value[:20]), self.assertRaises(workout_ai.InferenceError) as caught:
                    workout_ai._api_key(path)
                self.assertIn('API key is unavailable',str(caught.exception))
                self.assertNotIn(str(path),str(caught.exception))
            path.write_text('  fake-token  \n',encoding='utf-8')
            self.assertEqual(workout_ai._api_key(path),'fake-token')

    def test_missing_key_prevents_network_request(self):
        app = create_app({'TESTING':True,'SECRET_KEY':'test-only',
                          'WORKOUT_AI_API_KEY_FILE':'/nonexistent/synthetic/key'})
        with app.app_context(), patch('app.services.workout_ai._request') as send:
            with self.assertRaisesRegex(workout_ai.InferenceError,'API key is unavailable'):
                workout_ai.generate({}, {}, [])
            send.assert_not_called()
