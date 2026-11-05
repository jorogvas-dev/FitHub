# Validation — 2026-11-04

This is the original starter validation record. For the current Docker/PostgreSQL
and HTTPS/two-replica deployment checks, see [deployment validation](./validation-deployment.md).

10 unittest regression checks passed: public pages, dashboard authentication, CSRF including malformed non-ASCII tokens, parameterised exercise search, registration/password hashing, validation, duplicate accounts, login/logout, wrong passwords, owner-scoped dashboard queries, escaped output and dependency health statuses.

Database operations were mocked in web tests. The SQL schema passed PostgreSQL parser validation (10 tables). Compose YAML structure, Python source parsing and setup.sh syntax passed.

Not executed here: real PostgreSQL initialization, Docker build/start, Windows PowerShell setup and browser interaction. Docker/PowerShell are unavailable in the preparation workspace. Run the provided laptop setup and verify /health, registration, login, dashboard and exercise search. Expected database table count: 10.

The old starter is stopped only by the user command in README, with no volume deletion. New FitHub containers and volumes are a separate Compose project. No AI model/service is integrated yet.
