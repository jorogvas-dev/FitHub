# Backup and reproducibility

The submission backup is outside Git:

`E:\FitHub-School-Backups\fithub-20261107-124551-723c93fe.dump`

SHA256: `D14782CF0F58AE728392E31B476E964331AF87DC8A685F4CF44082074EBF610C`.
A checksum sidecar is stored beside it. This is a PostgreSQL custom-format backup
of the public application schema, excluding temporary regression schemas. It
contains private application data: keep it local, never upload it with the school
source. Environment secrets and Open WebUI/LM Studio are not part of this dump.

## Create and verify another backup

```powershell
Set-Location E:\FitHub-School
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup-postgres.ps1 -VerifyRestore
```

The script refuses a backup directory inside the repository. It writes the binary
dump in the container, copies it to the external directory (avoiding PowerShell
binary-redirection corruption), restores to a uniquely named temporary database,
compares row counts for every public table, and removes the temporary database
and container dump. The external backup/checksum remain. It was run successfully:
all **23 table counts matched**, and the restoration was cleaned up.

## Manual isolated restore

Choose a new temporary database name; never point this verification command at
the running `fithub` database. Use the database user configured in your deployment.

```powershell
$dump = 'E:\FitHub-School-Backups\fithub-20261107-124551-723c93fe.dump'
docker compose cp $dump db:/tmp/fithub-restore-check.dump
docker compose exec -T db createdb -U fithub fithub_restore_school_check
docker compose exec -T db pg_restore -U fithub --clean --if-exists --no-owner --no-privileges --exit-on-error --dbname fithub_restore_school_check /tmp/fithub-restore-check.dump
docker compose exec -T db psql -U fithub -d fithub_restore_school_check -c "SELECT count(*) FROM schema_migration;"
# Expected migration count: 13. Remove only the isolated verification database.
docker compose exec -T db dropdb -U fithub fithub_restore_school_check
docker compose exec -T db rm -f /tmp/fithub-restore-check.dump
```

The clean/if-exists flags also handle the public schema created by PostgreSQL's
database template. Replacing the live database is a separate deliberate recovery
operation; first stop writes, retain another backup and verify the destination.
Never test a restoration by overwriting live account data.

## Fresh installation verification

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-fresh-install.ps1
```

The verifier follows README's build → initialize/migrate → import/index → start →
HTTP-smoke sequence. It generates disposable secrets outside the repository, uses
a unique `fithub-fresh-xxxxxxxx` Compose project, separate PostgreSQL/Chroma/TLS
volumes and ports 18010/18443/18088. The API-key directory is empty deliberately.
No main deployment credentials or data are mounted into the fresh application.
Real HTTP checks register fictional data, exercise the core manual flows
and remove that account. The verifier then removes only its named project resources,
volumes, generated config/secrets and temporary images. Main resources are retained.

The recorded fresh project `fithub-fresh-8b57cd2f` completed successfully; its
containers, network, volumes and temporary image tags were confirmed absent.
Chroma's recipe index is reproducible from the licensed data and importer; it
contains no user data and need not be restored from a private vector-store backup.
