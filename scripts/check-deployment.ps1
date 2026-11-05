param([ValidateRange(0,60)][int]$RecoverySeconds=15, [string]$EvidenceDirectory)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'deployment-check-common.ps1')
Initialize-CheckEvidence $EvidenceDirectory

try {
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Invalid Compose configuration.' }
    $config = (docker compose config --format json | Out-String) | ConvertFrom-Json
    $details = @(Get-CheckWebDetails)
    if ($details.Count -ne 2) { throw 'Expected two running web replicas.' }
    # Compare Compose's platform image digest. Docker Desktop may expose an index
    # digest for an older container and an image/config digest for its replacement.
    $imageDigests = @($details | ForEach-Object { $_.Config.Labels.'com.docker.compose.image' })
    if (-not $imageDigests[0] -or $imageDigests[0] -ne $imageDigests[1]) {
        throw "Web replica image digests differ: $($imageDigests -join ', ')."
    }
    $secrets = @($details | ForEach-Object { @($_.Config.Env | Where-Object { $_ -like 'SECRET_KEY=*' })[0] })
    if (-not $secrets[0] -or $secrets[0] -ne $secrets[1]) { throw 'Web replicas must share the same SECRET_KEY.' }
    foreach ($detail in $details) {
        if (@($detail.HostConfig.PortBindings.PSObject.Properties).Count -gt 0) { throw 'Web ports must not be published.' }
        if ($detail.State.Health.Status -ne 'healthy') { throw 'Web replica is not healthy.' }
    }
    $null = Invoke-CheckDocker -CommandArgs @('compose','exec','-T','nginx','nginx','-t') -Name 'nginx-config'
    $null = Invoke-CheckDocker -CommandArgs @('compose','run','--rm','--no-deps','-T','--volume',"${PWD}:/srv:ro",
        '--env','FITHUB_TEST_DATABASE_URL=connect_timeout=3','web','python','-m','unittest','discover','-s','tests','-v') -Name 'regression-tests'
    $httpPort = [int]$config.services.nginx.ports[0].published
    $httpsPort = [int]$config.services.nginx.ports[1].published
    $logsPort = [int]$config.services.dozzle.ports[0].published
    $certPath = Join-Path $script:CheckEvidenceDirectory 'localhost-public.crt'
    try {
        $null = Invoke-CheckDocker -CommandArgs @('compose','cp','nginx:/etc/nginx/certs/localhost.crt',$certPath) -Name 'public-certificate'
        curl.exe --fail --silent --show-error --noproxy '*' --cacert $certPath "https://localhost:$httpsPort/health"
        if ($LASTEXITCODE -ne 0) { throw 'Host HTTPS/certificate validation failed.' }
        $redirect = curl.exe --silent --show-error --noproxy '*' --output NUL --write-out '%{http_code} %{redirect_url}' "http://localhost:$httpPort/health"
        if ($LASTEXITCODE -ne 0 -or $redirect -ne "308 https://localhost:$httpsPort/health") { throw "Host redirect failed: $redirect" }
        curl.exe --fail --silent --show-error --noproxy '*' --output NUL "http://localhost:$logsPort/"
        if ($LASTEXITCODE -ne 0) { throw 'Host Dozzle port unavailable.' }
    } finally {
        if (Test-Path -LiteralPath $certPath) { Remove-Item -LiteralPath $certPath }
    }
    Invoke-ProxyCheck -Phase 'two-replicas' -ExpectedReplicas 2 -HttpPort $httpPort -HttpsPort $httpsPort -RecoverySeconds $RecoverySeconds
    $stopped = $details[1]
    $phaseFailure = $null
    try {
        $null = Invoke-CheckDocker -CommandArgs @('stop',$stopped.Id) -Name 'stop-second-replica'
        Save-DeploymentState 'one-replica-start'
        Invoke-ProxyCheck -Phase 'one-replica' -ExpectedReplicas 1 -HttpPort $httpPort -HttpsPort $httpsPort -RecoverySeconds $RecoverySeconds -ExcludedIds @($stopped.Config.Hostname)
    } catch {
        $phaseFailure = $_
        throw
    } finally {
        try {
            # Readiness wait belongs inside finally; exceptions skip statements after finally.
            Restore-CheckWebReplicas 'one-replica'
        } catch {
            Save-DeploymentState 'restore-failed'
            if ($phaseFailure) { throw "Original check failure: $phaseFailure; cleanup failure: $_" }
            throw
        }
    }
    Invoke-ProxyCheck -Phase 'restored-replicas' -ExpectedReplicas 2 -HttpPort $httpPort -HttpsPort $httpsPort -RecoverySeconds $RecoverySeconds
    & (Join-Path $PSScriptRoot 'check-dns-recovery.ps1') -EvidenceDirectory $script:CheckEvidenceDirectory -RecoverySeconds $RecoverySeconds
    docker compose ps
    Write-Host "Deployment checks passed. Response reports and separate stdout/stderr: $script:CheckEvidenceDirectory"
} catch {
    Save-DeploymentState 'checker-failed'
    throw
}
