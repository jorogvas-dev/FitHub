$ErrorActionPreference = 'Stop'
$recipeRoot = Join-Path $PSScriptRoot '../db/data/unitools'
$recipeManifest = Get-Content (Join-Path $recipeRoot 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($recipeFile in @(@{ Name = 'unitools-recipes-v1.json'; Hash = $recipeManifest.sha256 }, @{ Name = 'LICENSE'; Hash = $recipeManifest.license_sha256 })) {
    $recipeTarget = Join-Path $recipeRoot $recipeFile.Name
    $recipeTemporary = Join-Path $recipeRoot ($recipeFile.Name + '.download')
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/farcrak/unitools-recipes/$($recipeManifest.commit)/$($recipeFile.Name)" -OutFile $recipeTemporary
    if ((Get-FileHash -LiteralPath $recipeTemporary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $recipeFile.Hash) {
        throw "Checksum mismatch for $($recipeFile.Name); existing source was preserved."
    }
    Move-Item -LiteralPath $recipeTemporary -Destination $recipeTarget -Force
}
Write-Output "Downloaded pinned source and license; both SHA256 checks passed. Commit $($recipeManifest.commit)"
