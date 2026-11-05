$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path .env)) { throw "Existing FitHub .env not found. Run from your installed FitHub project." }
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "Invalid Docker configuration." }
# Current application updates use additive, versioned migrations.
& (Join-Path $PSScriptRoot 'update-deployment.ps1')
if ($LASTEXITCODE -ne 0) { throw "Update failed. Run docker compose logs --tail 100 web." }
docker compose ps
Write-Host 'Workout editor ready at https://localhost:8443/plans (unless HTTPS_PORT was changed). HTTP redirects to HTTPS.'
