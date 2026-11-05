# Deployment validation — 2026-11-04

Executed directly on the Windows/Docker Desktop installation in `E:\FitHub-School`.

- All 36 existing unittest checks passed in the web image against PostgreSQL 18;
  zero skipped. Tests used unique temporary schemas.
- Both PowerShell update/check scripts executed successfully. All PowerShell scripts
  parsed without syntax errors. Compose validation, web/nginx builds and `nginx -t`
  passed. All six project containers are healthy.
- Initial port checks found only the project's old web listener on 8010. 8443 and
  8088 were free. A separate occupied-port probe confirmed that the updater aborts
  before build/start actions when a port belongs to an unrelated listener.
- Host curl validated HTTPS using the generated public certificate as its CA,
  including hostname checking. HTTP returned 308 to HTTPS with the configured port.
  Proxy checks also verified preservation of the original path and query string.
- Eight sequential `/health` requests alternated the two replica hostnames. Both
  reported healthy PostgreSQL and Chroma dependencies.
- Temporary account registration, logout/login, secure cookies, shared login on
  both replicas, a CSRF token created on one replica and accepted on the other,
  and rejection of a missing CSRF token passed. Probe accounts were removed.
- Stopping one web replica left HTTPS health and authenticated requests working
  through the remaining replica. The stopped replica was restarted in `finally`,
  and two-replica service/alternation was verified again.
- DNS recovery was verified by recreating one replica while holding its former IP
  with a disposable probe container. Its address changed from `172.21.0.4` to
  `172.21.0.8`. Both replicas then served requests without restarting nginx. The
  IP holder was removed, and two healthy replicas were restored.
- Dozzle's localhost UI and container log stream were opened in the browser. The
  FitHub project group showed its six containers; its configured Docker filter is
  `label=com.docker.compose.project=fithub-school`.
- All original `.env` entries matched their pre-update fingerprints. Only
  `HTTPS_PORT` and `DOZZLE_PORT` were added. The existing PostgreSQL and Chroma volume
  names were verified. Row fingerprints of all ten existing public tables matched
  the pre-deployment fingerprints after the probes; accounts, plans, profiles,
  bodyweight entries, sessions and historical logs were preserved.
- A second update reused the original certificate (valid 2026-11-04 through
  2027-11-04). No application code, schema or Python dependencies changed.

No requested deployment check was skipped. The self-signed certificate was not
installed in the system trust store; browser users accept its local warning.
Official sources and repeatable commands are in [the deployment guide](./deployment.md).
