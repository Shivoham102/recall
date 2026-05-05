# Usage: ./scripts/bump-version.ps1 0.2.0
# Updates version in tauri.conf.json, app/package.json, and Cargo.toml.
# Then prints the git commands to commit, tag, and push.

param([Parameter(Mandatory)][string]$Version)

$root = Split-Path $PSScriptRoot -Parent

# tauri.conf.json
$tauriPath = Join-Path $root "app\src-tauri\tauri.conf.json"
$tauri = Get-Content $tauriPath -Raw | ConvertFrom-Json
$tauri.version = $Version
$tauri | ConvertTo-Json -Depth 10 | Set-Content $tauriPath -Encoding utf8NoBOM
Write-Host "Updated $tauriPath"

# app/package.json
$pkgPath = Join-Path $root "app\package.json"
$pkg = Get-Content $pkgPath -Raw | ConvertFrom-Json
$pkg.version = $Version
$pkg | ConvertTo-Json -Depth 10 | Set-Content $pkgPath -Encoding utf8NoBOM
Write-Host "Updated $pkgPath"

# Cargo.toml (first [package] version line only)
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
$updated | Set-Content $cargoPath -Encoding utf8NoBOM
Write-Host "Updated $cargoPath"

Write-Host ""
Write-Host "Version bumped to $Version. Run:"
Write-Host "  git add -A"
Write-Host "  git commit -m `"chore: v$Version`""
Write-Host "  git tag v$Version"
Write-Host "  git push --follow-tags"
