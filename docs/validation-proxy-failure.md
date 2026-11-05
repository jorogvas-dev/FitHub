# Proxy verification failure investigation — 2026-11-04

## Reproduced failure

The reported summary was `Proxy checks failed with 1 replica(s).` at the former
line 38 of the PowerShell checker. The Python checker already guarded its alternation
assertion with `expect_replicas == 2`; the one-replica phase did not require the
stopped replica or alternating IDs.

The exact stop/probe phase was run with the original zero-grace health policy using
`--expect-replicas 1 --recovery-seconds 0`. It reproduced this real HTTP failure:

| UTC time | Request | Status | Upstream |
| --- | --- | --- | --- |
| 15:43:32.075552 | `GET https://nginx/health` | 200 | `172.21.0.5:8000` (survivor) |
| 15:43:34.078056 | `GET https://nginx/health` | 504 | `172.21.0.8:8000` (stopped replica's address) |

The returned body was captured verbatim:

```html
<html>
<head><title>504 Gateway Time-out</title></head>
<body>
<center><h1>504 Gateway Time-out</h1></center>
<hr><center>nginx</center>
</body>
</html>
```

Captured stderr identifies the exact assertion as `HTTPS health`, with status 504,
URL, upstream and body, and the Python traceback (`Unavailable` from `Probe.health`).
nginx logged `upstream timed out (110: Operation timed out) while connecting to upstream`
at the same time and address. It also logged an equivalent 504 at 15:12:48 UTC
during the earlier user-triggered stop.

The state snapshot taken before cleanup showed nginx, PostgreSQL, Chroma and web-1
running/healthy, while web-2 was exited. The reproduction exited nonzero as expected.
Its `finally` path then restored two healthy web replicas and waited for readiness.

Raw reproduction evidence on this machine is in:

```text
%TEMP%\fithub-check-<reproduction-run-id>
```

Files include `one-replica-no-grace.json`, separate `.stdout.txt`/`.stderr.txt`, and
`one-replica-no-grace-failed-{containers,states,logs}.stdout.txt`.

## Cause and limits of the evidence

There is a real transient gateway failure during peer removal/DNS convergence.
The checker assumed that every request must succeed immediately after a stop and
gave this transition no recovery budget. It therefore converted a brief convergence
window into a failed deployment verification. The evidence does not establish the
internal nginx peer-selection reason why that particular request exhausted retries;
no persistent nginx DNS/failover failure was observed. nginx configuration was left
unchanged.

A complete run of the old checker reproduced a separate false failure after restart:
`Replicas did not alternate: [survivor, survivor, restarted, survivor, ...]` even
though responses were healthy and both replicas appeared. Exact adjacent-request
alternation is an invalid convergence/concurrency assumption. The adjacent login
form/POST assertion had the same assumption.

The updated full run also exposed two independent image-reference problems in the
DNS checker: an old running container's manifest reference could not be used for
`docker run`, and Docker Desktop exposed different reference forms for an old and a
replacement web container despite equal Compose platform digests. These were checker
failures, not nginx failures. The failed DNS probe restored two healthy replicas
through its cleanup path; image lookup now happens before destructive probe steps.

## Changes

- Add a finite 15-second proxy recovery phase, followed by eight strictly successful
  health requests and strict authentication/CSRF checks. Persistent errors still fail.
- Pass the expected live replica IDs from Docker and explicitly exclude stopped or
  replaced IDs. One replica never requires two IDs. Two replicas require coverage
  of both live IDs, without an exact ordering assumption.
- Prove cross-replica CSRF using a bounded series of requests. No authentication
  mutation is automatically replayed after an HTTP failure.
- Save named-phase HTTP diagnostics, raw command stdout/stderr and container
  states/IPs/logs. Sensitive input values, cookies and environment variables are omitted.
- Wait for two healthy replicas inside `finally`; preserve the original error if
  cleanup also fails. DNS cleanup uses nested `finally` blocks.
- Resolve a usable local config image ID for the DNS IP-holder and compare stable
  Compose platform image digests for the identical-replica check.
- Add ten checker regression tests covering one/two-replica identity rules,
  non-alternating healthy responses, transient versus persistent failures, deadlines,
  stopped-node rejection, malformed responses, transport errors and redaction.

## Executed results

The full updated PowerShell checker passed, including all **46 tests, zero skipped**:
the existing 36 application/PostgreSQL checks and ten checker tests. It also passed
TLS certificate verification, redirect, Dozzle access, live authentication/CSRF,
one-replica availability, restart/readiness, and changed-IP DNS recovery without
restarting nginx (replacement IP changed from `172.21.0.6` to `172.21.0.8`).

Full-run response reports and raw stdout/stderr are in:

```text
%TEMP%\fithub-check-<full-run-id>
```

An additional real one-replica run with the 15-second budget passed eight strict
health requests plus authentication and restored two healthy replicas. Its requests
did not happen to return a 504; recovery from synthetic 504/503 and rejection of a
persistent outage were tested separately using `httpx.MockTransport` and a fake clock.
These simulated cases are not presented as live outages. The zero-grace 504 above
was a live reproduction, with no injected nginx fault.

All requested checks were executed. Secrets, volumes, application code, schema and
features were not changed; probe accounts were removed, and no database was reset.

Repeat the complete verification from PowerShell:

```powershell
Set-Location E:\FitHub-School
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-deployment.ps1
```
