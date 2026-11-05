param([string]$EvidenceDirectory, [ValidateRange(0,60)][int]$RecoverySeconds=15)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'deployment-check-common.ps1')
Initialize-CheckEvidence $EvidenceDirectory
$config = (docker compose config --format json | Out-String) | ConvertFrom-Json
$replicas = @(Get-CheckWebDetails)
if ($replicas.Count -ne 2) { throw 'DNS recovery check requires two healthy replicas.' }
$target = $replicas[1]
$network = @($target.NetworkSettings.Networks.PSObject.Properties)[0].Name
$oldIP = $target.NetworkSettings.Networks.$network.IPAddress
$nginxId = docker compose ps -q nginx
# A running container may reference a stale multi-platform manifest digest that cannot
# be used by docker run after a rebuild. Resolve the current local image's config ID.
$nginxImageName = $config.services.nginx.image
if (-not $nginxImageName) { $nginxImageName = $config.name + '-nginx' }
$localImage = (docker image inspect $nginxImageName | Out-String | ConvertFrom-Json)[0]
if ($LASTEXITCODE -ne 0 -or -not $localImage.Id) { throw 'Local nginx probe image missing; run the deployment updater first.' }
$nginxImage = $localImage.Id
$holder = 'fithub-dns-probe-' + [Guid]::NewGuid().ToString('N')
$phaseFailure = $null
try {
    $null = Invoke-CheckDocker -CommandArgs @('stop',$target.Id) -Name 'dns-stop'
    $null = Invoke-CheckDocker -CommandArgs @('rm',$target.Id) -Name 'dns-remove'
    $null = Invoke-CheckDocker -CommandArgs @('run','-d','--name',$holder,'--network',$network,'--ip',$oldIP,
        '--label','com.docker.compose.project=fithub-deployment-probe','--entrypoint','sleep',$nginxImage,'180') -Name 'dns-hold-old-ip'
    Restore-CheckWebReplicas 'dns-replacement'
    $replacement = (docker inspect $target.Name.TrimStart('/') | Out-String | ConvertFrom-Json)[0]
    $newIP = $replacement.NetworkSettings.Networks.$network.IPAddress
    if ($oldIP -eq $newIP) { throw 'Expected a different IP for the recreated replica.' }
    if ((docker compose ps -q nginx) -ne $nginxId) { throw 'nginx was unexpectedly recreated.' }
    Invoke-ProxyCheck -Phase 'dns-recovery' -ExpectedReplicas 2 -HttpPort $config.services.nginx.ports[0].published -HttpsPort $config.services.nginx.ports[1].published -RecoverySeconds $RecoverySeconds -ExcludedIds @($target.Config.Hostname)
    Write-Host "PASS: replica IP changed from $oldIP to $newIP; nginx recovered without restart."
} catch {
    $phaseFailure = $_
    Save-DeploymentState 'dns-recovery-failed'
    throw
} finally {
    # Nest finally so even failure to remove the probe cannot skip restoring the web service.
    try {
        $holderId = docker ps -aq --filter "name=^/$holder$"
        if ($holderId) { $null = Invoke-CheckDocker -CommandArgs @('rm','-f',$holderId) -Name 'dns-remove-holder' }
    } finally {
        try { Restore-CheckWebReplicas 'dns-cleanup' }
        catch {
            Save-DeploymentState 'dns-cleanup-failed'
            if ($phaseFailure) { throw "Original check failure: $phaseFailure; cleanup failure: $_" }
            throw
        }
    }
}
