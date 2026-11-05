$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath .env)) {
    throw 'Existing FitHub .env not found. Use setup.ps1 only for a new installation.'
}
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Invalid Compose configuration.' }
# Parse resolved settings without writing credentials to the terminal.
$config = (docker compose config --format json | Out-String) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Compose configuration.' }
if ($config.name -ne 'fithub-school') { throw 'Expected the existing fithub-school project. Remove any project-name override.' }
$bindings = @($config.services.nginx.ports) + @($config.services.dozzle.ports)
$ownedBindings = @{}
$projectIds = @(docker ps -q --filter 'label=com.docker.compose.project=fithub-school')
foreach ($containerId in $projectIds) {
    $info = (docker inspect $containerId | Out-String | ConvertFrom-Json)[0]
    # Only the old web listener or this project's proxy/log viewer may own these ports.
    if ($info.Config.Labels.'com.docker.compose.service' -notin @('web','nginx','dozzle')) { continue }
    foreach ($portProperty in $info.NetworkSettings.Ports.PSObject.Properties) {
        foreach ($binding in $portProperty.Value) {
            if ($binding.HostIp -eq '127.0.0.1') { $ownedBindings[[int]$binding.HostPort] = $true }
        }
    }
}
$seenPorts = @{}
foreach ($binding in $bindings) {
    $port = [int]$binding.published
    if ($seenPorts.ContainsKey($port)) { throw "Port $port is configured more than once. Set distinct WEB_PORT, HTTPS_PORT and DOZZLE_PORT values." }
    $seenPorts[$port] = $true
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($listener -and -not $ownedBindings.ContainsKey($port)) {
        throw "Port $port is already occupied (PID $($listener.OwningProcess -join ',')). Choose a free port in .env; no service was stopped."
    }
    if (-not $listener) {
        $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $port)
        try { $probe.Start() } catch { throw "Cannot bind localhost:$port. Select a free, non-reserved port in .env." } finally { $probe.Stop() }
    }
}
# Append only missing settings, preserving every existing byte and secret.
$envPath = Join-Path (Get-Location) '.env'
$envText = [IO.File]::ReadAllText($envPath)
$missing = @()
foreach ($setting in @('HTTPS_PORT=8443','DOZZLE_PORT=8088')) {
    $key = $setting.Split('=')[0]
    if ($envText -notmatch "(?m)^\s*$key\s*=") { $missing += $setting }
}
if ($missing.Count -gt 0) {
    $addition = "`r`n# Local reverse proxy and project logs`r`n" + ($missing -join "`r`n") + "`r`n"
    [IO.File]::AppendAllText($envPath, $addition, (New-Object Text.UTF8Encoding($false)))
}
docker compose build web nginx
if ($LASTEXITCODE -ne 0) { throw 'Image build failed; running containers were not changed.' }
docker compose pull dozzle
if ($LASTEXITCODE -ne 0) { throw 'Dozzle image download failed; running containers were not changed.' }
docker compose up -d --wait --wait-timeout 180 db chroma
if ($LASTEXITCODE -ne 0) { throw 'Database dependencies are not ready; web containers were not redeployed.' }
docker compose run --rm --no-deps -T web flask --app app db-upgrade
if ($LASTEXITCODE -ne 0) { throw 'Versioned database upgrade failed; web containers were not redeployed.' }
docker compose up -d --wait --wait-timeout 300
if ($LASTEXITCODE -ne 0) { throw 'Deployment failed. Inspect docker compose ps and docker compose logs --tail 100.' }
docker compose exec -T nginx nginx -t
if ($LASTEXITCODE -ne 0) { throw 'nginx configuration validation failed.' }
docker compose ps
$httpPort = $config.services.nginx.ports[0].published
$httpsPort = $config.services.nginx.ports[1].published
$logsPort = $config.services.dozzle.ports[0].published
Write-Host "FitHub: https://localhost:$httpsPort (self-signed certificate). HTTP: http://localhost:$httpPort redirects to HTTPS."
Write-Host "Project logs: http://localhost:$logsPort"
