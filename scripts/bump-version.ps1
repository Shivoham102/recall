# Usage: ./scripts/bump-version.ps1 0.7.0
# Bumps version in all three places, commits, tags, and pushes.
# Always run this before tagging a release — never tag manually.

param([Parameter(Mandatory)][string]$Version)

$root = Split-Path $PSScriptRoot -Parent
$utf8 = [System.Text.UTF8Encoding]::new($false)  # UTF-8 without BOM

# tauri.conf.json
$tauriPath = Join-Path $root "app\src-tauri\tauri.conf.json"
$tauri = Get-Content $tauriPath -Raw | ConvertFrom-Json
$tauri.version = $Version
$json = $tauri | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($tauriPath, $json, $utf8)
Write-Host "Updated $tauriPath"

# app/package.json
$pkgPath = Join-Path $root "app\package.json"
$content = Get-Content $pkgPath -Raw
$content = $content -replace '"version"\s*:\s*"[^"]*"', """version"": ""$Version"""
[System.IO.File]::WriteAllText($pkgPath, $content, $utf8)
Write-Host "Updated $pkgPath"

# app/src-tauri/Cargo.toml
$cargoPath = Join-Path $root "app\src-tauri\Cargo.toml"
$cargo = Get-Content $cargoPath
$inPackage = $false
$updated = foreach ($line in $cargo) {
    if ($line -match '^\[package\]') { $inPackage = $true }
    elseif ($line -match '^\[') { $inPackage = $false }
    if ($inPackage -and $line -match '^version\s*=') {
        "version = `"$Version`""
    } else {
        $line
    }
}
[System.IO.File]::WriteAllText($cargoPath, ($updated -join "`n") + "`n", $utf8)
Write-Host "Updated $cargoPath"

# Commit, tag, push
Set-Location $root
git add app/src-tauri/tauri.conf.json app/package.json app/src-tauri/Cargo.toml
git commit -m "chore: bump version to $Version"
git tag "v$Version"
git push origin master
git push origin "v$Version"

Write-Host ""
Write-Host "Released v$Version - GitHub Actions building installers now."
