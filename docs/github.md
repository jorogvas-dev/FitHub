# Publishing FitHub source to GitHub

Publish application source, setup/deployment configuration, placeholder-only
[.env.example](../.env.example), migrations, tests, documentation and the attributed
public USDA seed. GitHub does not need a copy of the running database or its volumes.
The existing environment, accounts, measurements, plans and logs stay on this machine.

## Files that must remain local

[.gitignore](../.gitignore) excludes environment files (except the root example),
credentials, certificates/private keys, database dumps, database/vector-store files,
logs, uploads, archives, private trainer PDFs, spreadsheets and common personal-data
directories. SQL is included only under the versioned initialization and migration
paths. Public USDA JSON under db/data is intentional and contains food reference
data, not user records. Test accounts and passwords are synthetic fixtures.

Keep personal exports in an ignored exports, private or personal-data directory.
Do not put user records into source, migrations, tests or the public food seed.
Ignore rules are a guardrail; they do not identify every possible sensitive string
or remove files that were committed previously. Review the actual staged contents
before every publication. Never regenerate or publish the existing live secrets as
part of preparing a source repository.

## Review before committing

From the project root in PowerShell:

```powershell
git status --short --ignored
git check-ignore -v .env
git ls-files -- .env.example db/init db/migrations tests docs
git diff --cached --stat
git diff --cached --check
git diff --cached
```

Check filenames and contents for environment values, access tokens, connection
strings containing passwords, private keys, personal paths, real contact details,
database exports and trainer documents. An empty search result is not a guarantee
that arbitrary personal information is absent. Environment placeholders and
deliberately fake test credentials must not be replaced with real values.

[.gitattributes](../.gitattributes) preserves LF line endings for shell scripts and
container configuration after a Windows clone. UI files are published as they are;
GitHub preparation does not redesign or modify the app.

## Connect the intended repository and push

Inspect existing remotes before adding one:

```powershell
git remote -v
git branch --show-current
git log -1 --format="%H"
```

When there is no remote, obtain the intended GitHub repository URL from the owner.
Do not select an account, repository name or visibility on their behalf. After the
owner supplies the URL, add it as origin if origin is still absent, inspect the
remote history and push the reviewed branch normally with git push -u origin main.
If the destination already contains commits, preserve them and reconcile histories
before pushing. Never force-push over existing work.

Use the existing Git/GitHub authentication; never embed a token in a remote URL.
Prefer the account's GitHub noreply address for commit metadata when keeping a
personal email private. Do not commit machine-specific Git configuration.

## Set up a fresh clone

Install/start Docker Desktop, open PowerShell in the cloned project and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Setup creates a local .env from the placeholder template only when .env is missing.
For the existing installation use the documented deployment updater instead; keep
its current .env and volumes. See [the project setup guide](../README.md) and
[nutrition migrations and checks](./nutrition.md). Publishing source does not move,
reset or expose application data.
