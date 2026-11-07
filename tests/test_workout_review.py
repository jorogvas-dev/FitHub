"""Mocked transport only; never contacts a live inference service."""
import json
import unittest
from unittest.mock import patch
from app import create_app
from app.services import workout_ai


class ReviewAdapterTests(unittest.TestCase):
    def test_nutrition_uses_separate_preset_and_same_authenticated_transport(self):
        app=create_app({'TESTING':True,'SECRET_KEY':'review-test','NUTRITION_AI_MODEL':'exact-coach-id',
                        'WORKOUT_AI_MODEL':'existing-workout-model','WORKOUT_AI_TIMEOUT_SECONDS':120})
        with app.app_context(),patch('app.services.workout_ai._api_key',return_value='synthetic-key'),patch('app.services.workout_ai._request',return_value='{}') as request:
            self.assertEqual(workout_ai.nutrition({},{}),'{}')
        self.assertEqual(request.call_args.args[1]['model'],'exact-coach-id')
        self.assertEqual(request.call_args.args[2],120)
        self.assertEqual(request.call_args.args[1]['tools'],[])

    def test_review_reuses_provider_model_key_deadline_and_disables_document_tools(self):
        app=create_app({'TESTING':True,'SECRET_KEY':'review-test',
            'WORKOUT_AI_ENDPOINT':'http://host.docker.internal:3000/api/chat/completions',
            'WORKOUT_AI_MODEL':'existing-model','WORKOUT_AI_TIMEOUT_SECONDS':120,'WORKOUT_AI_TOOL_IDS':'document_only'})
        raw='{"highlights":["fact_1"],"suggestions":[]}'
        with app.app_context(),patch('app.services.workout_ai._api_key',return_value='synthetic-key'),patch('app.services.workout_ai._request',return_value=raw) as request:
            self.assertEqual(workout_ai.review({'facts':[{'id':'fact_1'}]},{}),raw)
        endpoint,payload,timeout,key=request.call_args.args
        self.assertEqual(endpoint,app.config['WORKOUT_AI_ENDPOINT'])
        self.assertEqual(payload['model'],'existing-model')
        self.assertEqual((timeout,key),(120,'synthetic-key'))
        self.assertEqual(payload['tool_ids'],[])
        self.assertEqual(payload['tools'],[])
        self.assertNotIn('synthetic-key',json.dumps(payload))
        self.assertEqual(payload['response_format']['json_schema']['name'],'fithub_workout_review')

    def test_review_timeout_busy_and_oversize_input_are_bounded(self):
        app=create_app({'TESTING':True,'SECRET_KEY':'review-test','WORKOUT_AI_TIMEOUT_SECONDS':1})
        with app.app_context(),patch('app.services.workout_ai._api_key',return_value='synthetic-key'):
            with patch('app.services.workout_ai._request',side_effect=workout_ai.InferenceError('timed out')):
                with self.assertRaisesRegex(workout_ai.InferenceError,'timed out'):workout_ai.review({}, {})
            with patch('app.services.workout_ai._slots.acquire',return_value=False):
                with self.assertRaisesRegex(workout_ai.InferenceError,'busy'):workout_ai.review({}, {})
            with patch('app.services.workout_ai._request') as request:
                with self.assertRaises(workout_ai.InferenceError):workout_ai.review({'data':'x'*70000}, {})
                request.assert_not_called()
