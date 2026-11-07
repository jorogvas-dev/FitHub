param([string]$ProjectName = ('fithub-fresh-' + [Guid]::NewGuid().ToString('N').Substring(0,8)))
$ErrorActionPreference = 'Stop'
if ($ProjectName -notmatch '^fithub-fresh-[a-z0-9]{8}$') { throw 'Use an isolated fithub-fresh-xxxxxxxx project name.' }
foreach ($kind in @('container','network','volume')) {
    $existing = @(docker $kind ls -q --filter "label=com.docker.compose.project=$ProjectName")
    if ($LASTEXITCODE -ne 0) { throw 'Docker is unavailable.' }
    if ($existing.Count -gt 0) { throw 'This temporary project already has resources. Choose a new project name.' }
}
$repoPath = Split-Path -Parent $PSScriptRoot
$evidencePath = Join-Path ([IO.Path]::GetTempPath()) $ProjectName
New-Item -ItemType Directory -Force -Path $evidencePath | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $evidencePath 'empty-secrets') | Out-Null
function New-FreshSecret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-','').ToLowerInvariant()
}
$freshEnv = (Get-Content (Join-Path $repoPath '.env.example') -Raw).Replace('POSTGRES_PASSWORD=GENERATE_WITH_SETUP_SCRIPT',('POSTGRES_PASSWORD=' + (New-FreshSecret))).Replace('SECRET_KEY=GENERATE_WITH_SETUP_SCRIPT',('SECRET_KEY=' + (New-FreshSecret)))
$envFile = Join-Path $evidencePath '.env'
[IO.File]::WriteAllText($envFile,$freshEnv,(New-Object Text.UTF8Encoding($false)))
$emptySecrets = (Join-Path $evidencePath 'empty-secrets').Replace('\','/')
$overrideFile = Join-Path $evidencePath 'isolation.yaml'
$override = @"
services:
  db:
    ports: !reset []
  web:
    volumes: !override
      - type: bind
        source: $emptySecrets
        target: /run/secrets/fithub
        read_only: true
  nginx:
    environment:
      HTTPS_PORT: '18443'
    ports: !override
      - '127.0.0.1:18010:80'
      - '127.0.0.1:18443:443'
  dozzle:
    environment:
      DOZZLE_FILTER: label=com.docker.compose.project=$ProjectName
    ports: !override
      - '127.0.0.1:18088:8080'
"@
[IO.File]::WriteAllText($overrideFile,$override,(New-Object Text.UTF8Encoding($false)))
$freshCompose = @('compose','-p',$ProjectName,'--env-file',$envFile,'-f',(Join-Path $repoPath 'compose.yaml'),'-f',$overrideFile)
function Invoke-Fresh([string[]]$StepArgs) {
    & docker @freshCompose @StepArgs
    if ($LASTEXITCODE -ne 0) { throw 'Fresh installation verification step failed. Temporary resources will be cleaned up.' }
}
try {
    foreach ($port in @(18010,18443,18088)) {
        $probe = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,$port)
        try { $probe.Start() } finally { $probe.Stop() }
    }
    Invoke-Fresh -StepArgs @('config','--quiet')
    Invoke-Fresh -StepArgs @('build','web','nginx')
    Invoke-Fresh -StepArgs @('up','-d','--wait','--wait-timeout','180','db','chroma')
    Invoke-Fresh -StepArgs @('run','--rm','--no-deps','-T','web','flask','--app','app','db-upgrade')
    Invoke-Fresh -StepArgs @('run','--rm','--no-deps','-T','web','flask','--app','app','import-recipes','--dry-run')
    Invoke-Fresh -StepArgs @('run','--rm','--no-deps','-T','web','flask','--app','app','import-recipes','--index')
    Invoke-Fresh -StepArgs @('up','-d','--wait','--wait-timeout','240')
    Invoke-Fresh -StepArgs @('run','--rm','--no-deps','-T','--volume',"${repoPath}/scripts:/srv/scripts:ro",'web','python','scripts/smoke_submission.py')
    Write-Output 'Fresh installation passed with isolated volumes, fictional data and no AI credentials.'
} finally {
    & docker @freshCompose down --volumes --remove-orphans --rmi local
    $cleanupExit = $LASTEXITCODE
    # These exact files were created by this script outside the repository.
    Remove-Item -LiteralPath $envFile -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $overrideFile -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $evidencePath 'empty-secrets') -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $evidencePath -ErrorAction SilentlyContinue
    if ($cleanupExit -ne 0) { throw "Fresh-project cleanup failed for $ProjectName; inspect its resources." }
}
