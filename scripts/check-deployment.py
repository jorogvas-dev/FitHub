"""Probe the real proxy with bounded recovery and response-level diagnostics."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import sys
import time

import httpx
import psycopg


class CheckFailure(RuntimeError):
    pass


class Unavailable(CheckFailure):
    """Only transport errors and temporary gateway/dependency failures may recover."""


def response_details(response):
    # Never record request form bodies, cookies or CSRF/password input values.
    body = response.text
    def redact_input(match):
        tag = match[0]
        if re.search(r'name=["\'](?:csrf_token|password|confirm_password)["\']', tag, re.I):
            return re.sub(r'value=["\'][^"\']*["\']', 'value="[redacted]"', tag, flags=re.I)
        return tag
    body = re.sub(r'<input\b[^>]*>', redact_input, body, flags=re.I)
    return {
        'method': response.request.method, 'url': str(response.request.url),
        'status': response.status_code, 'upstream': response.headers.get('X-FitHub-Upstream', ''),
        'location': response.headers.get('location', ''),
        'content_type': response.headers.get('content-type', ''), 'body': body[:4096],
    }


def upstream(response):
    # nginx includes unsuccessful connection attempts before the successful address.
    return response.headers.get('X-FitHub-Upstream', '').split(',')[-1].strip()


class Probe:
    def __init__(self, phase, expected_count, expected_ids=(), excluded_ids=()):
        self.phase = phase
        self.expected_count = expected_count
        self.expected_ids = set(expected_ids)
        self.excluded_ids = set(excluded_ids)
        self.events = []
        self.last = None

    def require(self, condition, check, response=None, message=''):
        if not condition:
            detail = response_details(response) if response is not None else self.last
            raise CheckFailure(json.dumps(dict(phase=self.phase, check=check, message=message, response=detail)))

    def request(self, client, method, path, **kwargs):
        started = time.monotonic()
        try:
            response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            self.last = dict(method=method, url=str(exc.request.url), error=type(exc).__name__, message=str(exc))
            self.events.append(dict(time=datetime.now(timezone.utc).isoformat(), **self.last))
            raise Unavailable(json.dumps(dict(phase=self.phase, check='HTTP transport', response=self.last))) from exc
        self.last = response_details(response)
        self.events.append(dict(time=datetime.now(timezone.utc).isoformat(),
                                elapsed_ms=round((time.monotonic()-started)*1000), **self.last))
        return response

    def health(self, client, **kwargs):
        response = self.request(client, 'GET', '/health', **kwargs)
        if response.status_code in (502, 503, 504):
            raise Unavailable(json.dumps(dict(phase=self.phase, check='HTTPS health', response=self.last)))
        self.require(response.status_code == 200, 'HTTPS health status', response)
        try:
            body = response.json()
        except ValueError:
            self.require(False, 'HTTPS health JSON', response)
        self.require(isinstance(body, dict), 'HTTPS health object', response)
        services = body.get('services', {})
        self.require(isinstance(services, dict), 'dependency health object', response)
        self.require(body.get('status') == 'ok' and services.get('postgresql') == 'ok'
                     and services.get('chromadb') == 'ok', 'dependency health', response)
        replica = body.get('replica')
        self.require(isinstance(replica, str) and bool(replica), 'health replica ID', response)
        self.require(replica not in self.excluded_ids, 'stopped replica responded', response)
        self.require(not self.expected_ids or replica in self.expected_ids, 'unexpected replica responded',
                     response, f'Expected live IDs: {sorted(self.expected_ids)}')
        self.require(bool(upstream(response)), 'health upstream header', response)
        return replica, upstream(response)

    def recover(self, client, seconds, clock=time.monotonic, sleep=time.sleep):
        if seconds == 0:
            return
        deadline = clock() + seconds
        consecutive = 0
        seen = set()
        while clock() < deadline:
            try:
                replica, _ = self.health(client, timeout=min(5, max(0.05, deadline-clock())))
            except Unavailable as exc:
                consecutive = 0
                seen.clear()
                print('RECOVERY: '+str(exc), file=sys.stderr, flush=True)
            else:
                consecutive += 1
                seen.add(replica)
                if consecutive >= 3 and len(seen) == self.expected_count:
                    print(f'PASS [{self.phase}]: proxy recovered within {seconds}s; live IDs={sorted(seen)}', flush=True)
                    return
            sleep(min(0.25, max(0, deadline-clock())))
        self.require(False, 'proxy recovery deadline', message=f'Not ready within {seconds}s; IDs={sorted(seen)}')

    def steady_health(self, client, samples=8):
        records = [self.health(client) for _ in range(samples)]
        ids = [row[0] for row in records]
        addresses = {row[1] for row in records}
        self.require(len(set(ids)) == self.expected_count, 'steady replica coverage',
                     message=f'Observed {ids}; expected {self.expected_count} live replicas')
        self.require(len(addresses) == self.expected_count, 'steady upstream coverage',
                     message=f'Observed addresses: {sorted(addresses)}')
        # Round robin is shared by all clients; recovery and other traffic can consume turns.
        print(f'PASS [{self.phase}]: {samples} consecutive healthy requests; replica IDs: '+', '.join(ids), flush=True)
        return addresses

    def csrf_token(self, response):
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
        self.require(match is not None, 'CSRF form token', response)
        return match[1]

    def authenticate(self, client, addresses):
        response = self.request(client, 'GET', '/dashboard')
        self.require(response.status_code == 302 and response.headers.get('location', '').endswith('/login'),
                     'anonymous dashboard protection', response)
        username = 'deploy_' + secrets.token_hex(8)
        email = username + '@example.invalid'
        password = secrets.token_urlsafe(24)
        try:
            registration = self.request(client, 'GET', '/register')
            response = self.request(client, 'POST', '/register', data=dict(csrf_token=self.csrf_token(registration),
                                    username=username, email=email, password=password, confirm_password=password))
            self.require(response.status_code == 302, 'temporary account registration', response)
            self.require('secure' in response.headers.get('set-cookie', '').lower(), 'Secure session cookie', response)
            authenticated = set()
            for _ in range(8):
                response = self.request(client, 'GET', '/dashboard')
                self.require(response.status_code == 200 and username in response.text, 'authenticated dashboard', response)
                self.require(upstream(response) in addresses, 'authenticated upstream', response)
                authenticated.add(upstream(response))
            self.require(authenticated == addresses, 'authenticated replica coverage',
                         message=f'Expected {sorted(addresses)}, got {sorted(authenticated)}')
            response = self.request(client, 'POST', '/logout', data={'csrf_token': self.csrf_token(response)})
            self.require(response.status_code == 302, 'logout', response)
            login = self.request(client, 'GET', '/login')
            response = self.request(client, 'POST', '/login', data=dict(csrf_token=self.csrf_token(login),
                                    username=username, password=password))
            self.require(response.status_code == 302, 'login', response)
            # Prove cross-replica CSRF without assuming two adjacent requests always use different peers.
            crossed = False
            for _ in range(6):
                form = self.request(client, 'GET', '/profile')
                self.require(form.status_code == 200, 'authenticated profile form', form)
                response = self.request(client, 'POST', '/profile', data={'csrf_token': self.csrf_token(form)})
                self.require(response.status_code == 302, 'valid profile CSRF', response)
                self.require(upstream(form) in addresses and upstream(response) in addresses, 'CSRF upstream', response)
                crossed |= upstream(form) != upstream(response)
                if crossed or self.expected_count == 1:
                    break
            self.require(self.expected_count == 1 or crossed, 'cross-replica CSRF coverage')
            response = self.request(client, 'POST', '/profile', data={})
            self.require(response.status_code == 400, 'missing CSRF rejection', response)
            print(f'PASS [{self.phase}]: registration, login, CSRF and authentication on {self.expected_count} live replica(s)', flush=True)
        finally:
            pending_failure = sys.exc_info()[0] is not None
            try:
                with psycopg.connect() as conn:
                    conn.execute('DELETE FROM app_user WHERE username=%s AND email=%s', (username, email))
            except Exception as exc:
                print(f'Account cleanup failed: {type(exc).__name__}: {exc}', file=sys.stderr)
                if not pending_failure:
                    raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expect-replicas', type=int, choices=[1, 2], default=2)
    parser.add_argument('--expected-replica', action='append', default=[])
    parser.add_argument('--excluded-replica', action='append', default=[])
    parser.add_argument('--recovery-seconds', type=float, default=15)
    parser.add_argument('--phase', default='proxy')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--https-port', type=int, default=8443)
    parser.add_argument('--http-port', type=int, default=8010)
    args = parser.parse_args()
    if not 0 <= args.recovery_seconds <= 60:
        parser.error('recovery-seconds must be between 0 and 60')
    if args.expected_replica and len(set(args.expected_replica)) != args.expect_replicas:
        parser.error('expected-replica IDs must match expect-replicas')
    probe = Probe(args.phase, args.expect_replicas, args.expected_replica, args.excluded_replica)
    report = dict(phase=args.phase, expected_ids=args.expected_replica, excluded_ids=args.excluded_replica)
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
            response = probe.request(client, 'GET', 'http://nginx/plans?check=redirect',
                                     headers={'Host': f'localhost:{args.http_port}'})
            probe.require(response.status_code == 308, 'HTTP redirect status', response)
            probe.require(response.headers.get('location') == f'https://localhost:{args.https_port}/plans?check=redirect',
                          'HTTP redirect destination', response)
        with httpx.Client(base_url='https://nginx', verify=False, trust_env=False, follow_redirects=False,
                          headers={'Host': f'localhost:{args.https_port}'}, timeout=5) as client:
            probe.recover(client, args.recovery_seconds)
            addresses = probe.steady_health(client)
            probe.authenticate(client, addresses)
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = probe.request(client, 'GET', 'http://dozzle:8080/')
            probe.require(response.status_code == 200 and 'dozzle' in response.text.lower(), 'Dozzle UI', response)
        report['status'] = 'passed'
        print(f'PASS [{args.phase}]: redirect, HTTPS, availability and Dozzle', flush=True)
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        print('FAIL: '+json.dumps(dict(phase=args.phase, error=str(exc), last_response=probe.last)), file=sys.stderr, flush=True)
        raise
    finally:
        if args.report:
            args.report.write_text(json.dumps(dict(report, requests=probe.events), indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
