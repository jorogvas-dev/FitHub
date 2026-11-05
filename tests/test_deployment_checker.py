"""Checker contracts: finite convergence, strict availability and useful diagnostics."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import runpy
import unittest

import httpx

CHECKER = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/check-deployment.py'))
Probe = CHECKER['Probe']
CheckFailure = CHECKER['CheckFailure']


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def healthy(replica='live'):
    return httpx.Response(200, json=dict(status='ok', replica=replica,
                          services=dict(postgresql='ok', chromadb='ok')),
                          headers={'X-FitHub-Upstream': replica+':8000'})


class DeploymentCheckerTests(unittest.TestCase):
    def setUp(self):
        # Synthetic 504/503 diagnostics should not resemble live deployment failures.
        self.enterContext(redirect_stdout(StringIO()))
        self.enterContext(redirect_stderr(StringIO()))

    def client(self, responses):
        pending = iter(responses)
        client = httpx.Client(base_url='https://nginx', transport=httpx.MockTransport(lambda req: next(pending)))
        self.addCleanup(client.close)
        return client

    def test_one_replica_accepts_repeated_survivor_ids(self):
        probe = Probe('one-replica', 1, ['live'], ['stopped'])
        addresses = probe.steady_health(self.client([healthy() for _ in range(8)]))
        self.assertEqual(addresses, {'live:8000'})
        self.assertEqual(len(probe.events), 8)

    def test_two_replicas_need_coverage_without_strict_alternation(self):
        ids = ['a', 'a', 'b', 'a', 'b', 'b', 'a', 'b']
        probe = Probe('restored', 2, ['a', 'b'])
        self.assertEqual(probe.steady_health(self.client([healthy(x) for x in ids])), {'a:8000', 'b:8000'})

    def test_two_replicas_cannot_pass_with_only_one_serving(self):
        probe = Probe('two-replicas', 2, ['a', 'b'])
        with self.assertRaisesRegex(CheckFailure, 'steady replica coverage'):
            probe.steady_health(self.client([healthy('a') for _ in range(8)]))

    def test_steady_phase_does_not_retry_or_hide_a_gateway_failure(self):
        probe = Probe('one-replica', 1, ['live'])
        client = self.client([healthy(), httpx.Response(504, text='<h1>Gateway Time-out</h1>',
                              headers={'X-FitHub-Upstream':'stopped:8000'}), healthy()])
        with self.assertRaises(CheckFailure) as failure:
            probe.steady_health(client)
        message = str(failure.exception)
        for value in ['one-replica', 'HTTPS health', '504', 'Gateway Time-out', 'stopped:8000', 'https://nginx/health']:
            self.assertIn(value, message)
        self.assertEqual(len(probe.events), 2)

    def test_bounded_recovery_accepts_transient_gateway_errors(self):
        probe = Probe('one-replica', 1, ['live'], ['stopped'])
        clock = Clock()
        client = self.client([httpx.Response(504, text='gateway timeout'), httpx.Response(503, text='warming'),
                              healthy(), healthy(), healthy()])
        probe.recover(client, 2, clock=clock, sleep=clock.sleep)
        self.assertLess(clock.now, 2)
        self.assertEqual([event['status'] for event in probe.events], [504, 503, 200, 200, 200])

    def test_permanent_outage_fails_within_recovery_budget(self):
        probe = Probe('one-replica', 1, ['live'])
        clock = Clock()
        with self.assertRaisesRegex(CheckFailure, 'proxy recovery deadline') as failure:
            probe.recover(self.client([httpx.Response(504, text='gateway timeout') for _ in range(10)]),
                          2, clock=clock, sleep=clock.sleep)
        self.assertEqual(clock.now, 2)
        self.assertIn('gateway timeout', str(failure.exception))
        self.assertEqual(len(probe.events), 8)

    def test_stopped_replica_response_is_rejected_even_during_recovery(self):
        probe = Probe('one-replica', 1, ['live'], ['stopped'])
        clock = Clock()
        with self.assertRaisesRegex(CheckFailure, 'stopped replica responded'):
            probe.recover(self.client([healthy('stopped')]), 2, clock=clock, sleep=clock.sleep)
        self.assertEqual(clock.now, 0)

    def test_malformed_health_and_unexpected_status_are_not_recovery_successes(self):
        for response, label in [(httpx.Response(200, text='not JSON'), 'health JSON'),
                                (httpx.Response(404, text='missing'), 'health status'),
                                (httpx.Response(200, json={'services':None}), 'dependency health object')]:
            with self.subTest(label=label):
                with self.assertRaisesRegex(CheckFailure, label):
                    Probe('test', 1).recover(self.client([response]), 2)

    def test_diagnostics_redact_csrf_and_password_values_and_preserve_response(self):
        body = '<form><input name="csrf_token" value="csrf-secret"><input name="password" value="password-secret"></form>'
        probe = Probe('authentication', 1)
        response = probe.request(self.client([httpx.Response(400, text=body)]), 'POST', '/login')
        with self.assertRaises(CheckFailure) as failure:
            probe.require(False, 'login', response)
        message = str(failure.exception)
        self.assertIn('400', message)
        self.assertIn('redacted', message)
        self.assertNotIn('csrf-secret', message)
        self.assertNotIn('password-secret', message)

    def test_transport_error_has_phase_url_and_remains_bounded(self):
        def unavailable(request):
            raise httpx.ConnectError('connection refused', request=request)
        client = httpx.Client(base_url='https://nginx', transport=httpx.MockTransport(unavailable))
        self.addCleanup(client.close)
        probe = Probe('one-replica', 1)
        clock = Clock()
        with self.assertRaisesRegex(CheckFailure, 'proxy recovery deadline') as failure:
            probe.recover(client, 1, clock=clock, sleep=clock.sleep)
        self.assertEqual(clock.now, 1)
        self.assertIn('https://nginx/health', str(failure.exception))
        self.assertIn('ConnectError', str(failure.exception))
