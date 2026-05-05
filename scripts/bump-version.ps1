# Usage: ./scripts/bump-version.ps1 0.2.0
# Updates version in tauri.conf.json, app/package.json, and Cargo.toml.
# Then prints the git commands to commit, tag, and push.

param([Parameter(Mandatory)][string]$Version)

$root = Split-Path $PSScriptRoot -Parent
$utf8 = [System.Text.UTF8Encoding]::new($false)  # UTF-8 without BOM

# tauri.conf.json — rewrite as clean JSON (no BOM, no escaped ampersands)
$tauriPath = Join-Path $root "app\src-tauri\tauri.conf.json"
$tauri = Get-Content $tauriPath -Raw | ConvertFrom-Json
$tauri.version = $Version
$json = $tauri | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($tauriPath, $json, $utf8)
Write-Host "Updated $tauriPath"

# app/package.json — patch version field only, preserve formatting
$pkgPath = Join-Path $root "app\package.json"
$content = Get-Content $pkgPath -Raw
$content = $content -replace '"version"\s*:\s*"[^"]*"', """version"": ""$Version"""
[System.IO.File]::WriteAllText($pkgPath, $content, $utf8)
Write-Host "Updated $pkgPath"

# Cargo.toml — patch first version line under [package]
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

Write-Host ""
Write-Host "Version bumped to $Version. Run:"
Write-Host "  git add -A"
Write-Host "  git commit -m `"chore: v$Version`""
Write-Host "  git tag v$Version"
Write-Host "  git push origin master"
Write-Host "  git push origin v$Version"
