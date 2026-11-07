param([string]$BackupDirectory = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'FitHub-School-Backups'),[switch]$VerifyRestore)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$repoRoot = [IO.Path]::GetFullPath((Get-Location).Path).TrimEnd('\')
$backupRoot = [IO.Path]::GetFullPath($BackupDirectory).TrimEnd('\')
if ($backupRoot -eq $repoRoot -or $backupRoot.StartsWith($repoRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Backups must be outside the Git repository.' }
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
$config = (docker compose config --format json | Out-String) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot read deployment configuration.' }
$dbUser = $config.services.db.environment.POSTGRES_USER
$dbName = $config.services.db.environment.POSTGRES_DB
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$nonce = [Guid]::NewGuid().ToString('N').Substring(0,8)
$filename = "fithub-$stamp-$nonce.dump"
$containerFile = "/tmp/$filename"
$backupFile = Join-Path $backupRoot $filename
$temporaryDatabase = "fithub_restore_$nonce"
$created = $false
try {
    docker compose exec -T db pg_dump -U $dbUser -d $dbName --schema=public --format=custom --file $containerFile
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed.' }
    docker compose cp "db:$containerFile" $backupFile
    if ($LASTEXITCODE -ne 0) { throw 'Backup copy failed.' }
    $checksum = (Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash
    "$checksum  $filename" | Set-Content -Encoding ascii -LiteralPath ($backupFile+'.sha256.txt')
    if ($VerifyRestore) {
        docker compose exec -T db createdb -U $dbUser $temporaryDatabase
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated restore database.' }
        $created = $true
        docker compose exec -T db pg_restore -U $dbUser --clean --if-exists --no-owner --no-privileges --exit-on-error --dbname $temporaryDatabase $containerFile
        if ($LASTEXITCODE -ne 0) { throw 'Restore verification failed.' }
        $countSql = "SELECT jsonb_object_agg(tablename,((xpath('/row/c/text()',query_to_xml(format('SELECT count(*) AS c FROM public.%I',tablename),false,true,'')))[1])::text::bigint) FROM pg_tables WHERE schemaname='public';"
        $sourceCounts = docker compose exec -T db psql -XAt -U $dbUser -d $dbName -c $countSql
        if ($LASTEXITCODE -ne 0) { throw 'Source count verification failed.' }
        $restoreCounts = docker compose exec -T db psql -XAt -U $dbUser -d $temporaryDatabase -c $countSql
        if ($LASTEXITCODE -ne 0 -or $sourceCounts -ne $restoreCounts) { throw 'Restored counts differ. If accounts were edited during backup, repeat in a quiet window.' }
        Write-Output 'Isolated restore succeeded; all public table row counts match.'
    }
    Write-Output "Backup: $backupFile"
    Write-Output "SHA256: $checksum"
} finally {
    if ($created) {
        if ($temporaryDatabase -notmatch '^fithub_restore_[a-f0-9]{8}$') { throw 'Refusing unsafe restore cleanup.' }
        docker compose exec -T db dropdb -U $dbUser $temporaryDatabase
        if ($LASTEXITCODE -ne 0) { throw "Could not remove isolated database $temporaryDatabase." }
    }
    if ($containerFile -match '^/tmp/fithub-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}\.dump$') { docker compose exec -T db rm -f $containerFile }
}
