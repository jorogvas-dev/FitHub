$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Install Docker Desktop, start it, then run this script again. See README.md."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Start Docker Desktop and wait for Engine running." }
if (-not (Test-Path .env)) {
    function New-Secret {
        $bytes = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
    }
    $content = (Get-Content .env.example -Raw).Replace('POSTGRES_PASSWORD=GENERATE_WITH_SETUP_SCRIPT', "POSTGRES_PASSWORD=$(New-Secret)").Replace('SECRET_KEY=GENERATE_WITH_SETUP_SCRIPT', "SECRET_KEY=$(New-Secret)")
    [IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $content, (New-Object Text.UTF8Encoding($false)))
}
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "Compose configuration is invalid." }
docker compose pull db chroma
if ($LASTEXITCODE -ne 0) { throw "Database image download failed." }
& (Join-Path $PSScriptRoot 'update-deployment.ps1')
if ($LASTEXITCODE -ne 0) { throw "Startup failed. Inspect docker compose logs." }
docker compose ps
Write-Host 'Ready. Default: https://localhost:8443 (self-signed certificate); http://localhost:8010 redirects. Logs: http://localhost:8088. Check port settings in .env if changed.'
