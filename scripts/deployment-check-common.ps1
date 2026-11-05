# Shared checker utilities. Diagnostics deliberately exclude container environment variables.
function Initialize-CheckEvidence([string]$Directory) {
    if (-not $Directory) {
        $Directory = Join-Path ([IO.Path]::GetTempPath()) ('fithub-check-' + [Guid]::NewGuid().ToString('N'))
    }
    $script:CheckEvidenceDirectory = [IO.Path]::GetFullPath($Directory)
    $null = New-Item -ItemType Directory -Force -Path $script:CheckEvidenceDirectory
    Write-Host "Deployment evidence: $script:CheckEvidenceDirectory"
}

function ConvertTo-CheckArgument([string]$Value) {
    if ($Value -ne '' -and $Value -notmatch '[\s"]') { return $Value }
    # Windows CommandLineToArgvW quoting, including backslashes before quotes/end.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-CheckDocker {
    param([string[]]$CommandArgs, [string]$Name, [switch]$AllowFailure, [switch]$Quiet)
    $safeName = $Name -replace '[^a-zA-Z0-9_.-]', '_'
    $stdoutPath = Join-Path $script:CheckEvidenceDirectory ($safeName + '.stdout.txt')
    $stderrPath = Join-Path $script:CheckEvidenceDirectory ($safeName + '.stderr.txt')
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = (Get-Command docker -ErrorAction Stop).Source
    $info.Arguments = ($CommandArgs | ForEach-Object { ConvertTo-CheckArgument $_ }) -join ' '
    $info.WorkingDirectory = (Get-Location).Path
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        $null = $process.Start()
        # Drain both pipes concurrently; Docker progress on stderr is not itself failure.
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $code = $process.ExitCode
    } finally { $process.Dispose() }
    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($stdoutPath, $stdout, $encoding)
    [IO.File]::WriteAllText($stderrPath, $stderr, $encoding)
    if ($stdout -and -not $Quiet) { Write-Host $stdout.TrimEnd() }
    if ($stderr -and -not $Quiet) { Write-Host $stderr.TrimEnd() }
    if ($code -ne 0 -and -not $AllowFailure) {
        throw "[$Name] docker exited $code. stdout: $stdoutPath; stderr: $stderrPath`n$stderr"
    }
    return [PSCustomObject]@{ ExitCode=$code; Stdout=$stdout; Stderr=$stderr; StdoutPath=$stdoutPath; StderrPath=$stderrPath }
}

function Get-CheckWebDetails {
    $ids = @(docker compose ps -q web)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot list web replicas.' }
    foreach ($containerId in $ids) {
        $detail = (docker inspect $containerId | Out-String | ConvertFrom-Json)[0]
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect web replica $containerId." }
        $detail
    }
}

function Save-DeploymentState([string]$Name) {
    try {
        $null = Invoke-CheckDocker -CommandArgs @('compose','ps','--all','--format','json') -Name "$Name-containers" -AllowFailure -Quiet
        $ids = @(docker compose ps --all -q)
        if ($ids.Count -gt 0) {
            $null = Invoke-CheckDocker -CommandArgs (@('inspect','--format','{{.Name}} {{.Config.Hostname}} {{json .State}} {{json .NetworkSettings.Networks}}') + $ids) -Name "$Name-states" -AllowFailure -Quiet
        }
        $null = Invoke-CheckDocker -CommandArgs @('compose','logs','--no-color','--since','5m','--tail','100','nginx','web','dozzle') -Name "$Name-logs" -AllowFailure -Quiet
        Write-Host "Container states/IPs and logs saved: $script:CheckEvidenceDirectory ($Name)"
    } catch { Write-Warning "Could not capture all container diagnostics: $_" }
}

function Restore-CheckWebReplicas([string]$Name) {
    $null = Invoke-CheckDocker -CommandArgs @('compose','up','-d','--no-deps','--scale','web=2','--wait','--wait-timeout','180','web') -Name "$Name-restore"
    $restored = @(Get-CheckWebDetails)
    if ($restored.Count -ne 2 -or @($restored | Where-Object { $_.State.Health.Status -ne 'healthy' }).Count -gt 0) {
        throw 'Cleanup did not restore two healthy web replicas.'
    }
    Write-Host "PASS [$Name]: cleanup restored two healthy web replicas."
}

function Invoke-ProxyCheck {
    param([string]$Phase, [int]$ExpectedReplicas, [int]$HttpPort, [int]$HttpsPort,
          [int]$RecoverySeconds=15, [string[]]$ExcludedIds=@())
    $live = @(Get-CheckWebDetails)
    if ($live.Count -ne $ExpectedReplicas) { throw "[$Phase] Expected $ExpectedReplicas running containers, got $($live.Count)." }
    $probeArgs = @('compose','run','--rm','--no-deps','-T','--volume',"${PWD}:/srv:ro",
        '--volume',"${script:CheckEvidenceDirectory}:/evidence",'web','python','scripts/check-deployment.py',
        '--expect-replicas',"$ExpectedReplicas",'--https-port',"$HttpsPort",'--http-port',"$HttpPort",
        '--phase',$Phase,'--recovery-seconds',"$RecoverySeconds",'--report',"/evidence/$Phase.json")
    foreach ($replica in $live) { $probeArgs += @('--expected-replica', $replica.Config.Hostname) }
    foreach ($excluded in $ExcludedIds) { $probeArgs += @('--excluded-replica', $excluded) }
    $result = Invoke-CheckDocker -CommandArgs $probeArgs -Name $Phase -AllowFailure
    if ($result.ExitCode -ne 0) {
        # Capture the stopped/recovering state BEFORE the caller's finally restores it.
        Save-DeploymentState "$Phase-failed"
        throw "[$Phase] Proxy check failed (exit $($result.ExitCode)). HTTP details: $script:CheckEvidenceDirectory\$Phase.json; stdout: $($result.StdoutPath); stderr: $($result.StderrPath)`n$($result.Stderr)"
    }
}
